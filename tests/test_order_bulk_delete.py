"""DB deletion and API validation without touching production records."""
import ast
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from uuid import UUID
from flask import Flask, request, jsonify

ROOT = Path(__file__).resolve().parents[1]


class BulkDeleteTests(unittest.TestCase):
    def setUp(self):
        path = Path(os.environ.get('ORDER_DELETE_SERVER_PATH', ROOT / 'server.py'))
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PostgresProcessOrderStorage')
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('delete_order', 'delete_orders')]
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_process_orders_bulk_delete')
        app = Flask(__name__)
        class Missing(Exception): pass
        ns = dict(app=app, request=request, jsonify=jsonify, os=os, ProcessOrderNotFoundError=Missing,
                  read_json_payload=lambda: request.get_json(silent=True) or {},
                  valid_order_id=lambda s: str(UUID(str(s))) if isinstance(s, str) and len(s) == 36 else '')
        # Use the implementation's actual UUID validation, including invalid input handling.
        validator = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'valid_order_id')
        import uuid
        ns['uuid'] = uuid
        orders_route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_process_orders')
        ns['normalize_process_product'] = lambda s: s if s in ('A', 'B') else ''
        exec(compile(ast.Module(body=[cls, validator, route, orders_route], type_ignores=[]), str(path), 'exec'), ns)
        self.storage = ns['PostgresProcessOrderStorage']()
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('PRAGMA foreign_keys=ON; CREATE TABLE process_orders(order_id TEXT PRIMARY KEY); CREATE TABLE process_judgments(order_id TEXT REFERENCES process_orders(order_id) ON DELETE RESTRICT);')
        self.ids = [str(UUID(int=i)) for i in (1, 2, 3)]
        self.db.executemany('INSERT INTO process_orders VALUES (?)', [(x,) for x in self.ids])
        self.db.executemany('INSERT INTO process_judgments VALUES (?)', [(x,) for x in self.ids])
        self.db.commit()
        owner = self
        class Connection:
            def __enter__(self): return self
            def __exit__(self, typ, *_):
                owner.db.rollback() if typ else owner.db.commit()
            def execute(self, sql, params=()):
                if sql.startswith('SET LOCAL'): return None
                sql = sql.replace('%s', '?').replace(' FOR UPDATE', '')
                result = owner.db.execute(sql, params)
                if getattr(owner, 'fail_after_delete', False) and sql.startswith('DELETE FROM process_orders'):
                    raise RuntimeError('simulated failure after SQL deletion')
                return result
        self.storage._connect = Connection
        ns['process_storage'] = lambda: self.storage
        self.client = app.test_client()
        self.headers = {'X-Order-Delete': 'permanent', 'Origin': 'http://localhost'}

    def tearDown(self): self.db.close()

    def count(self, table): return self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def test_selected_records_and_judgments_are_deleted_from_db(self):
        selected = self.ids[:2]
        r = self.client.post('/api/process-orders/bulk-delete', json=dict(order_ids=selected, confirm_order_ids=selected), headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['deleted_count'], 2)
        for table in ('process_orders', 'process_judgments'):
            self.assertEqual(self.db.execute('SELECT order_id FROM '+table).fetchall(), [(self.ids[2],)])

    def test_failure_rolls_back_both_tables(self):
        self.fail_after_delete = True
        with self.assertRaises(RuntimeError): self.storage.delete_orders(self.ids[:2])
        self.assertEqual(self.count('process_orders'), 3)
        self.assertEqual(self.count('process_judgments'), 3)

    def test_missing_order_preserves_selection(self):
        with self.assertRaises(Exception): self.storage.delete_orders([self.ids[0], str(UUID(int=99))])
        self.assertEqual(self.count('process_judgments'), 3)

    def test_render_https_origin_is_accepted_only_for_configured_service(self):
        with patch.dict(os.environ, {'RENDER_EXTERNAL_URL': 'https://ml-cylinder.onrender.com'}):
            payload = dict(order_ids=[], confirm_order_ids=[])
            headers = {**self.headers, 'Origin': 'https://ml-cylinder.onrender.com'}
            self.assertEqual(self.client.post('/api/process-orders/bulk-delete', json=payload, headers=headers).status_code, 400)
            headers['Origin'] = 'https://elsewhere.test'
            self.assertEqual(self.client.post('/api/process-orders/bulk-delete', json=payload, headers=headers).status_code, 403)
        self.assertEqual(self.count('process_orders'), 3)

    def test_render_blocks_creation_before_accessing_database_but_preserves_listing(self):
        calls = []
        self.storage.create_order = lambda *args: calls.append(args) or {'order_id': self.ids[0]}
        self.storage.list_orders = lambda limit: [{'order_id': self.ids[0]}]
        with patch.dict(os.environ, {'RENDER': 'true'}):
            r = self.client.post('/api/process-orders', json={'product_name': 'test', 'requested_product': 'A'})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json['error'], 'local_order_creation_required')
            self.assertEqual(self.client.get('/api/process-orders').status_code, 200)
        self.assertFalse(calls)
        with patch.dict(os.environ, {'RENDER': 'false'}):
            self.assertEqual(self.client.post('/api/process-orders', json={'product_name': 'test', 'requested_product': 'A'}).status_code, 201)
        self.assertEqual(calls, [('test', 'A')])

    def test_invalid_confirmation_origin_and_selection_never_delete(self):
        cases = [({}, self.headers, 400),
                 (dict(order_ids=self.ids, confirm_order_ids=[]), self.headers, 400),
                 (dict(order_ids=self.ids, confirm_order_ids=self.ids), {}, 403),
                 (dict(order_ids=self.ids, confirm_order_ids=self.ids), {**self.headers, 'Origin': 'https://elsewhere.test'}, 403),
                 (dict(order_ids=[self.ids[0]]*2, confirm_order_ids=[self.ids[0]]*2), self.headers, 400),
                 (dict(order_ids=['invalid'], confirm_order_ids=['invalid']), self.headers, 400)]
        for payload, headers, status in cases:
            self.assertEqual(self.client.post('/api/process-orders/bulk-delete', json=payload, headers=headers).status_code, status)
        self.assertEqual(self.count('process_orders'), 3)


if __name__ == '__main__': unittest.main()
