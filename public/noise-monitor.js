(function () {
  window.renderNoiseComparison = function (data, draw) {
    const el = id => document.getElementById(id);
    if (!el('noiseRmsState')) return;
    const rows = (data.history || []).filter(r => r.session_id === data.latest?.session_id);
    const valid = (r, sensor) => Number.isFinite(r.noise_reduction?.sensors?.[sensor]?.rms)
      && Number.isFinite(r.noise_reduction?.sensors?.[sensor]?.original_ac_rms);
    for (const sensor of ['sph0645', 'inmp441']) {
      const before = [], after = [];
      let previous = null;
      for (const row of rows) {
        if (previous !== null && row.sequence_id !== previous + 1) { before.push(null); after.push(null); }
        before.push(valid(row, sensor) ? row.noise_reduction.sensors[sensor].original_ac_rms : null);
        after.push(valid(row, sensor) ? row.noise_reduction.sensors[sensor].rms : null);
        previous = row.sequence_id;
      }
      draw(el(sensor + 'NoiseChart'), [
        {values: before, color: '#a7adb7'},
        {values: after, color: sensor === 'sph0645' ? '#42c7ff' : '#ffc04c'},
      ]);
    }
    const compared = [...rows].reverse().find(r => valid(r, 'sph0645') && valid(r, 'inmp441'));
    const labels = {READY: 'Pi 소음 보정값 연결됨 · 실험용', STALE: '보정 갱신 지연 · 과거 결과',
      UNAVAILABLE: '보정값 조회 실패', REFERENCE_DATA_REQUIRED: '이 장치의 소음 기준 없음'};
    el('noiseRmsState').textContent = labels[data.noise_reduction_status] || '같은 패킷의 보정 결과 대기';
    if (!compared) {
      el('noiseRmsValues').textContent = '보정 전·후 비교 데이터 대기';
      el('noiseRmsTime').textContent = '보정 측정 시각 없음';
      return;
    }
    const format = v => v.toLocaleString('ko-KR', {maximumFractionDigits: 2});
    el('noiseRmsValues').textContent = ['sph0645','inmp441'].map(sensor => {
      const v = compared.noise_reduction.sensors[sensor];
      return `${sensor.toUpperCase()}: ${format(v.original_ac_rms)} → ${format(v.rms)}`;
    }).join(' / ');
    const time = new Date(compared.noise_reduction.source_received_at)
      .toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'});
    el('noiseRmsTime').textContent = `같은 패킷 비교 · SEQ ${compared.sequence_id} · ${time} KST`;
  };
})();
