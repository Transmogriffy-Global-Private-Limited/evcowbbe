-- Domain entities schema migration for Charger on Wheels (Annex 11, 15, 44).
-- Implements customer profile & address, driver profile & documents, chargers,
-- service vehicles, fare rules, wallets & transactions, bookings, reports, and ratings.

-- 1. Service Settings (Singleton ID = 1)
CREATE TABLE IF NOT EXISTS service_settings (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  minimum_recharge_amount_minor bigint NOT NULL DEFAULT 10000 CHECK (minimum_recharge_amount_minor > 0),
  wallet_low_balance_threshold_minor bigint NOT NULL DEFAULT 5000 CHECK (wallet_low_balance_threshold_minor >= 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  updated_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO service_settings (id, minimum_recharge_amount_minor, wallet_low_balance_threshold_minor, currency)
VALUES (1, 10000, 5000, 'INR')
ON CONFLICT (id) DO NOTHING;

-- 2. Customer Profile
CREATE TABLE IF NOT EXISTS customer_profiles (
  account_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  full_name text NOT NULL CHECK (length(btrim(full_name)) BETWEEN 1 AND 150),
  phone text CHECK (phone IS NULL OR length(btrim(phone)) BETWEEN 7 AND 20),
  profile_picture_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- 3. Customer Address
CREATE TABLE IF NOT EXISTS customer_addresses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  label text NOT NULL CHECK (length(btrim(label)) BETWEEN 1 AND 50),
  recipient_name text,
  recipient_phone text,
  address_line_1 text NOT NULL CHECK (length(btrim(address_line_1)) >= 1),
  address_line_2 text,
  landmark text,
  locality text,
  city text NOT NULL,
  state text NOT NULL,
  postal_code text NOT NULL CHECK (length(btrim(postal_code)) BETWEEN 3 AND 12),
  country text NOT NULL DEFAULT 'IND',
  latitude double precision CHECK (latitude IS NULL OR (latitude BETWEEN -90 AND 90)),
  longitude double precision CHECK (longitude IS NULL OR (longitude BETWEEN -180 AND 180)),
  is_default boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customer_addresses_account ON customer_addresses(customer_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_addresses_default ON customer_addresses(customer_account_id) WHERE is_default = true;

-- 4. Driver Profile
CREATE TABLE IF NOT EXISTS driver_profiles (
  account_id uuid PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  full_name text NOT NULL CHECK (length(btrim(full_name)) BETWEEN 1 AND 150),
  phone text NOT NULL CHECK (length(btrim(phone)) BETWEEN 7 AND 20),
  date_of_birth date,
  profile_picture_key text,
  emergency_contact_name text,
  emergency_contact_phone text,
  residential_address_line_1 text,
  residential_address_line_2 text,
  residential_landmark text,
  residential_city text,
  residential_state text,
  residential_postal_code text,
  residential_country text NOT NULL DEFAULT 'IND',
  residential_latitude double precision CHECK (residential_latitude IS NULL OR (residential_latitude BETWEEN -90 AND 90)),
  residential_longitude double precision CHECK (residential_longitude IS NULL OR (residential_longitude BETWEEN -180 AND 180)),
  verification_status text NOT NULL DEFAULT 'DRAFT' CHECK (verification_status IN ('DRAFT','PENDING','VERIFIED','REJECTED')),
  lifecycle_status text NOT NULL DEFAULT 'ACTIVE' CHECK (lifecycle_status IN ('ACTIVE','BANNED','DELETED')),
  availability_status text NOT NULL DEFAULT 'OFFLINE' CHECK (availability_status IN ('OFFLINE','AVAILABLE','ON_JOB')),
  profile_completed_at timestamptz,
  verification_requested_at timestamptz,
  reviewed_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  reviewed_at timestamptz,
  rejection_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_driver_profiles_status ON driver_profiles(verification_status, lifecycle_status, availability_status);

-- 5. Driver Documents
CREATE TABLE IF NOT EXISTS driver_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  driver_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  document_type text NOT NULL CHECK (document_type IN ('AADHAAR','PAN','DRIVING_LICENSE','ADDRESS_PROOF','POLICE_VERIFICATION','BANK_PROOF')),
  version_no integer NOT NULL DEFAULT 1 CHECK (version_no >= 1),
  document_number_ciphertext text,
  document_number_last4 text CHECK (document_number_last4 IS NULL OR length(document_number_last4) <= 4),
  file_storage_key text NOT NULL,
  issued_on date,
  expires_on date,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
  is_current boolean NOT NULL DEFAULT true,
  submitted_at timestamptz NOT NULL DEFAULT now(),
  reviewed_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  reviewed_at timestamptz,
  rejection_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_driver_documents_driver ON driver_documents(driver_account_id, document_type, is_current);

-- 6. Chargers (Hardware Catalog)
CREATE TABLE IF NOT EXISTS chargers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  charger_code text NOT NULL UNIQUE CHECK (length(btrim(charger_code)) BETWEEN 2 AND 50),
  name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
  description text,
  charger_type text NOT NULL CHECK (charger_type IN ('PORTABLE_AC','PORTABLE_DC','FAST_DC','OTHER')),
  connector_type text,
  max_power_kw double precision CHECK (max_power_kw IS NULL OR max_power_kw > 0),
  supported_vehicle_types jsonb NOT NULL DEFAULT '["CAR"]'::jsonb,
  image_storage_key text,
  is_active boolean NOT NULL DEFAULT true,
  created_by_admin_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  updated_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chargers_active ON chargers(is_active, charger_type);

-- 7. Service Vehicles (Company-owned charging fleet)
CREATE TABLE IF NOT EXISTS service_vehicles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  vehicle_code text NOT NULL UNIQUE CHECK (length(btrim(vehicle_code)) BETWEEN 2 AND 50),
  registration_number text NOT NULL UNIQUE CHECK (length(btrim(registration_number)) BETWEEN 2 AND 50),
  display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 100),
  make text,
  model text,
  color text,
  operational_status text NOT NULL DEFAULT 'AVAILABLE' CHECK (operational_status IN ('AVAILABLE','IN_SERVICE','MAINTENANCE','RETIRED')),
  current_latitude double precision CHECK (current_latitude IS NULL OR (current_latitude BETWEEN -90 AND 90)),
  current_longitude double precision CHECK (current_longitude IS NULL OR (current_longitude BETWEEN -180 AND 180)),
  location_updated_at timestamptz,
  internal_notes text,
  created_by_admin_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  updated_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_service_vehicles_status ON service_vehicles(operational_status);

-- 8. Distance Fare Rules
CREATE TABLE IF NOT EXISTS distance_fare_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
  min_distance_km double precision NOT NULL CHECK (min_distance_km >= 0),
  max_distance_km double precision CHECK (max_distance_km IS NULL OR max_distance_km > min_distance_km),
  fare_amount_minor bigint NOT NULL CHECK (fare_amount_minor >= 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  is_active boolean NOT NULL DEFAULT true,
  active_from timestamptz NOT NULL DEFAULT now(),
  active_until timestamptz,
  created_by_admin_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  updated_by_admin_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_distance_fare_rules_active ON distance_fare_rules(is_active, active_from);

-- 9. Wallets (Customer closed-loop balance)
CREATE TABLE IF NOT EXISTS wallets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_account_id uuid NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
  balance_minor bigint NOT NULL DEFAULT 0 CHECK (balance_minor >= 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallets_customer ON wallets(customer_account_id);

-- 10. Wallet Recharge Orders
CREATE TABLE IF NOT EXISTS wallet_recharge_orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  wallet_id uuid NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
  provider text NOT NULL,
  idempotency_key text NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) >= 8),
  provider_order_id text,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  status text NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','PENDING','PAID','FAILED','CANCELLED','REFUNDED')),
  provider_order_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  provider_created_at timestamptz,
  paid_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_recharge_orders_wallet ON wallet_recharge_orders(wallet_id, status);
CREATE INDEX IF NOT EXISTS idx_wallet_recharge_orders_provider_order ON wallet_recharge_orders(provider_order_id);

-- 11. Wallet Recharge Payments
CREATE TABLE IF NOT EXISTS wallet_recharge_payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  recharge_order_id uuid NOT NULL REFERENCES wallet_recharge_orders(id) ON DELETE CASCADE,
  provider_payment_id text NOT NULL UNIQUE,
  provider_order_id text NOT NULL,
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  status text NOT NULL CHECK (status IN ('AUTHORIZED','CAPTURED','FAILED','REFUNDED')),
  payment_method text,
  signature_verified boolean NOT NULL DEFAULT false,
  error_code text,
  error_description text,
  provider_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  authorized_at timestamptz,
  captured_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_recharge_payments_order ON wallet_recharge_payments(recharge_order_id);

-- 12. Service Bookings
CREATE TABLE IF NOT EXISTS service_bookings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_code text NOT NULL UNIQUE CHECK (length(btrim(booking_code)) BETWEEN 6 AND 30),
  customer_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  customer_address_id uuid REFERENCES customer_addresses(id) ON DELETE SET NULL,
  customer_vehicle_id uuid REFERENCES customer_vehicles(id) ON DELETE SET NULL,
  charger_id uuid NOT NULL REFERENCES chargers(id) ON DELETE RESTRICT,
  distance_fare_rule_id uuid REFERENCES distance_fare_rules(id) ON DELETE SET NULL,
  assigned_driver_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  assigned_service_vehicle_id uuid REFERENCES service_vehicles(id) ON DELETE SET NULL,
  idempotency_key text NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) >= 8),
  service_address_text text NOT NULL,
  service_latitude double precision CHECK (service_latitude IS NULL OR (service_latitude BETWEEN -90 AND 90)),
  service_longitude double precision CHECK (service_longitude IS NULL OR (service_longitude BETWEEN -180 AND 180)),
  customer_vehicle_type_snapshot text,
  customer_vehicle_make_snapshot text,
  customer_vehicle_model_snapshot text,
  customer_vehicle_registration_snapshot text,
  charger_name_snapshot text NOT NULL,
  charger_type_snapshot text NOT NULL,
  distance_km double precision NOT NULL DEFAULT 0 CHECK (distance_km >= 0),
  fare_amount_minor bigint NOT NULL CHECK (fare_amount_minor >= 0),
  currency text NOT NULL DEFAULT 'INR' CHECK (length(currency) = 3),
  fare_rule_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'BOOKED' CHECK (status IN ('BOOKED','ASSIGNED','DRIVER_EN_ROUTE','ARRIVED','CHARGING','COMPLETED','CANCELLED','FAILED')),
  payment_status text NOT NULL DEFAULT 'PAID' CHECK (payment_status IN ('PAID','PARTIALLY_REFUNDED','REFUNDED')),
  paid_at timestamptz NOT NULL DEFAULT now(),
  refund_amount_minor bigint NOT NULL DEFAULT 0 CHECK (refund_amount_minor >= 0),
  customer_note text,
  admin_note text,
  requested_at timestamptz NOT NULL DEFAULT now(),
  assigned_at timestamptz,
  driver_en_route_at timestamptz,
  driver_arrived_at timestamptz,
  charging_started_at timestamptz,
  charging_completed_at timestamptz,
  completed_at timestamptz,
  cancelled_at timestamptz,
  cancellation_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_service_bookings_customer ON service_bookings(customer_account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_service_bookings_driver ON service_bookings(assigned_driver_account_id, status);
CREATE INDEX IF NOT EXISTS idx_service_bookings_status ON service_bookings(status, requested_at);

-- 13. Booking Assignments
CREATE TABLE IF NOT EXISTS booking_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL REFERENCES service_bookings(id) ON DELETE CASCADE,
  driver_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  service_vehicle_id uuid NOT NULL REFERENCES service_vehicles(id) ON DELETE RESTRICT,
  assigned_by_admin_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  status text NOT NULL DEFAULT 'ASSIGNED' CHECK (status IN ('ASSIGNED','ACCEPTED','REJECTED','CANCELLED','REASSIGNED','COMPLETED')),
  assigned_at timestamptz NOT NULL DEFAULT now(),
  accepted_at timestamptz,
  ended_at timestamptz,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_booking_assignments_booking ON booking_assignments(booking_id, status);
CREATE INDEX IF NOT EXISTS idx_booking_assignments_driver ON booking_assignments(driver_account_id, status);

-- 14. Wallet Transactions (Immutable Ledger)
CREATE TABLE IF NOT EXISTS wallet_transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  wallet_id uuid NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
  transaction_type text NOT NULL CHECK (transaction_type IN ('RECHARGE_CREDIT','BOOKING_DEBIT','BOOKING_REFUND','ADMIN_CREDIT','ADMIN_DEBIT','RECHARGE_REVERSAL')),
  amount_minor bigint NOT NULL CHECK (amount_minor > 0),
  balance_before_minor bigint NOT NULL CHECK (balance_before_minor >= 0),
  balance_after_minor bigint NOT NULL CHECK (balance_after_minor >= 0),
  booking_id uuid REFERENCES service_bookings(id) ON DELETE SET NULL,
  recharge_order_id uuid REFERENCES wallet_recharge_orders(id) ON DELETE SET NULL,
  actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  idempotency_key text NOT NULL UNIQUE CHECK (length(btrim(idempotency_key)) >= 8),
  description text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wallet_transactions_wallet ON wallet_transactions(wallet_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wallet_transactions_booking ON wallet_transactions(booking_id);

-- 15. Booking Lifecycle Events (Audit Timeline)
CREATE TABLE IF NOT EXISTS booking_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL REFERENCES service_bookings(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  previous_status text,
  next_status text,
  actor_plane text NOT NULL CHECK (actor_plane IN ('app','driver','admin','system')),
  actor_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,
  reason text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_booking_events_booking ON booking_events(booking_id, created_at ASC);

-- 16. Booking Service Reports (Post-charging delivery proof)
CREATE TABLE IF NOT EXISTS booking_service_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL UNIQUE REFERENCES service_bookings(id) ON DELETE CASCADE,
  driver_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  energy_delivered_kwh double precision CHECK (energy_delivered_kwh IS NULL OR energy_delivered_kwh >= 0),
  battery_percentage_before double precision CHECK (battery_percentage_before IS NULL OR (battery_percentage_before BETWEEN 0 AND 100)),
  battery_percentage_after double precision CHECK (battery_percentage_after IS NULL OR (battery_percentage_after BETWEEN 0 AND 100)),
  service_notes text,
  completion_photo_storage_key text,
  customer_acknowledged_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_booking_service_reports_driver ON booking_service_reports(driver_account_id);

-- 17. Driver Location Telemetry
CREATE TABLE IF NOT EXISTS driver_location_updates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  driver_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  booking_id uuid REFERENCES service_bookings(id) ON DELETE SET NULL,
  latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  heading_degrees double precision CHECK (heading_degrees IS NULL OR (heading_degrees BETWEEN 0 AND 360)),
  speed_kph double precision CHECK (speed_kph IS NULL OR speed_kph >= 0),
  recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_driver_location_updates_driver ON driver_location_updates(driver_account_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_driver_location_updates_booking ON driver_location_updates(booking_id, recorded_at DESC);

-- 18. Booking Ratings & Reviews
CREATE TABLE IF NOT EXISTS booking_ratings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL UNIQUE REFERENCES service_bookings(id) ON DELETE CASCADE,
  customer_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  driver_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  overall_rating smallint NOT NULL CHECK (overall_rating BETWEEN 1 AND 5),
  driver_rating smallint CHECK (driver_rating IS NULL OR (driver_rating BETWEEN 1 AND 5)),
  service_rating smallint CHECK (service_rating IS NULL OR (service_rating BETWEEN 1 AND 5)),
  charger_rating smallint CHECK (charger_rating IS NULL OR (charger_rating BETWEEN 1 AND 5)),
  review_text text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_booking_ratings_driver ON booking_ratings(driver_account_id, overall_rating);
CREATE INDEX IF NOT EXISTS idx_booking_ratings_customer ON booking_ratings(customer_account_id);

-- 19. Admin Audit Logs
CREATE TABLE IF NOT EXISTS admin_audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id text,
  reason text,
  before_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  after_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_admin ON admin_audit_logs(admin_account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_entity ON admin_audit_logs(entity_type, entity_id);

