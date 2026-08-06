-- Supabase SQL Editor에서 한 번 실행한다. 원시 샘플은 업로드하지 않는다.
CREATE TABLE IF NOT EXISTS public.smart_cylinder_analysis (
  measurement_id uuid PRIMARY KEY,
  device_id text NOT NULL,
  sensor_type text NOT NULL CHECK (sensor_type IN ('sph0645', 'inmp441')),
  measured_at timestamptz NOT NULL,
  cylinder_state text NOT NULL CHECK (cylinder_state IN ('forward', 'backward', 'idle')),
  vibration_rms double precision,
  sound_rms double precision,
  dominant_frequency double precision NOT NULL,
  dominant_amplitude double precision NOT NULL,
  prediction text NOT NULL CHECK (prediction IN ('normal','pressure_drop','seal_leak','internal_wear','unknown')),
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  health_score double precision NOT NULL CHECK (health_score BETWEEN 0 AND 100),
  model_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS smart_cylinder_device_time_idx
  ON public.smart_cylinder_analysis (device_id, measured_at DESC);
ALTER TABLE public.smart_cylinder_analysis ENABLE ROW LEVEL SECURITY;
-- Pi에 service_role 키를 사용할 경우 별도 정책이 필요 없다. anon 키를 쓸 경우 최소 INSERT 정책을 직접 추가한다.
