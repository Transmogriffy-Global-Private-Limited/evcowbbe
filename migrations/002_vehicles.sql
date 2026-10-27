-- Customer Vehicle registration schema
CREATE TABLE IF NOT EXISTS customer_vehicles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name text,
  vehicle_type text NOT NULL DEFAULT 'CAR' CHECK (vehicle_type IN ('CAR', 'MOTORCYCLE', 'SCOOTER', 'THREE_WHEELER', 'OTHER')),
  make text,
  model text,
  registration_number text NOT NULL CHECK (length(registration_number) BETWEEN 2 AND 50),
  battery_capacity_kwh double precision CHECK (battery_capacity_kwh IS NULL OR battery_capacity_kwh > 0),
  is_default boolean NOT NULL DEFAULT false,
  last_charged timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_customer_vehicle_registration UNIQUE (customer_account_id, registration_number)
);

CREATE INDEX IF NOT EXISTS idx_customer_vehicles_account ON customer_vehicles(customer_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_vehicles_default ON customer_vehicles(customer_account_id) WHERE is_default = true;

-- Compatibility view mapping to CMS/fleet terminology
CREATE OR REPLACE VIEW vehicles AS
  SELECT
    id,
    customer_account_id AS customer_id,
    name,
    vehicle_type AS type,
    make,
    model,
    registration_number AS vehicle_number,
    battery_capacity_kwh,
    is_default,
    last_charged,
    created_at AS date_added,
    created_at,
    updated_at
  FROM customer_vehicles;

