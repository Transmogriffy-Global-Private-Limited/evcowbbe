// Package customer implements the repository pattern for customer entities.
package customer

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/lib/pq"
	"github.com/shopspring/decimal"
)

// Repository specifies data operations for customer profile, vehicles, and addresses.
type Repository interface {
	// Profile operations
	GetProfileByAccountID(ctx context.Context, accountID uuid.UUID) (*CustomerProfile, error)
	GetProfileByID(ctx context.Context, id uuid.UUID) (*CustomerProfile, error)
	EnsureProfile(ctx context.Context, accountID uuid.UUID) (*CustomerProfile, error)
	CreateProfile(ctx context.Context, accountID uuid.UUID, req *CreateProfileRequest, actorAccountID *uuid.UUID) (*CustomerProfile, error)
	UpdateProfile(ctx context.Context, profileID uuid.UUID, req *UpdateProfileRequest, actorAccountID *uuid.UUID) (*CustomerProfile, error)

	// Vehicle operations
	CreateVehicle(ctx context.Context, customerID uuid.UUID, req *CreateVehicleRequest, actorAccountID *uuid.UUID) (*CustomerVehicle, error)
	GetVehicleByID(ctx context.Context, customerID, vehicleID uuid.UUID) (*CustomerVehicle, error)
	ListVehicles(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]CustomerVehicle, error)
	UpdateVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, req *UpdateVehicleRequest, actorAccountID *uuid.UUID) (*CustomerVehicle, error)
	DeleteVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, actorAccountID *uuid.UUID) error

	// Address operations
	CreateAddress(ctx context.Context, customerID uuid.UUID, req *CreateAddressRequest, actorAccountID *uuid.UUID) (*CustomerAddress, error)
	GetAddressByID(ctx context.Context, customerID, addressID uuid.UUID) (*CustomerAddress, error)
	ListAddresses(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]CustomerAddress, error)
	UpdateAddress(ctx context.Context, customerID, addressID uuid.UUID, req *UpdateAddressRequest, actorAccountID *uuid.UUID) (*CustomerAddress, error)
	DeleteAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error
	SetDefaultAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error
}

// PgRepository implements Repository using *pgxpool.Pool and atomic transactions.
type PgRepository struct {
	Pool *pgxpool.Pool
}

func NewRepository(pool *pgxpool.Pool) Repository {
	return &PgRepository{Pool: pool}
}

// GetProfileByAccountID retrieves the customer profile by account_id.
func (r *PgRepository) GetProfileByAccountID(ctx context.Context, accountID uuid.UUID) (*CustomerProfile, error) {
	row := r.Pool.QueryRow(ctx, `
		SELECT id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
		FROM customer_profiles
		WHERE account_id = $1 AND plane = 'app'
	`, accountID)

	var p CustomerProfile
	err := row.Scan(&p.ID, &p.AccountID, &p.Plane, &p.FullName, &p.PrimaryPhone, &p.Status, &p.CreatedAt, &p.UpdatedAt, &p.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

// GetProfileByID retrieves the customer profile by primary key id.
func (r *PgRepository) GetProfileByID(ctx context.Context, id uuid.UUID) (*CustomerProfile, error) {
	row := r.Pool.QueryRow(ctx, `
		SELECT id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
		FROM customer_profiles
		WHERE id = $1
	`, id)

	var p CustomerProfile
	err := row.Scan(&p.ID, &p.AccountID, &p.Plane, &p.FullName, &p.PrimaryPhone, &p.Status, &p.CreatedAt, &p.UpdatedAt, &p.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

// EnsureProfile guarantees that an INCOMPLETE profile exists for an app account.
func (r *PgRepository) EnsureProfile(ctx context.Context, accountID uuid.UUID) (*CustomerProfile, error) {
	existing, err := r.GetProfileByAccountID(ctx, accountID)
	if err == nil {
		return existing, nil
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, err
	}

	newID := uuid.New()
	row := r.Pool.QueryRow(ctx, `
		INSERT INTO customer_profiles (id, account_id, plane, status, version)
		VALUES ($1, $2, 'app', 'INCOMPLETE', 1)
		ON CONFLICT (account_id) DO UPDATE SET updated_at = now()
		RETURNING id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
	`, newID, accountID)

	var p CustomerProfile
	if err := row.Scan(&p.ID, &p.AccountID, &p.Plane, &p.FullName, &p.PrimaryPhone, &p.Status, &p.CreatedAt, &p.UpdatedAt, &p.Version); err != nil {
		return nil, err
	}
	return &p, nil
}

// CreateProfile creates a new profile record or updates an incomplete bootstrap profile.
func (r *PgRepository) CreateProfile(ctx context.Context, accountID uuid.UUID, req *CreateProfileRequest, actorAccountID *uuid.UUID) (*CustomerProfile, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var currentID uuid.UUID
	var currentStatus string
	err = tx.QueryRow(ctx, `
		SELECT id, status FROM customer_profiles WHERE account_id = $1 FOR UPDATE
	`, accountID).Scan(&currentID, &currentStatus)
	if err == nil {
		if currentStatus != "INCOMPLETE" {
			return nil, fmt.Errorf("%w: customer profile already exists", ErrConflict)
		}
		newStatus := "INCOMPLETE"
		var fullName *string
		if req.FullName != nil && len(strings.TrimSpace(*req.FullName)) > 0 {
			t := strings.TrimSpace(*req.FullName)
			fullName = &t
			newStatus = "ACTIVE"
		}
		var phone *string
		if req.PrimaryPhone != nil && len(strings.TrimSpace(*req.PrimaryPhone)) > 0 {
			t := strings.TrimSpace(*req.PrimaryPhone)
			phone = &t
		}
		var p CustomerProfile
		err = tx.QueryRow(ctx, `
			UPDATE customer_profiles
			SET full_name = $1, primary_phone = $2, status = $3, version = version + 1, updated_at = now()
			WHERE id = $4
			RETURNING id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
		`, fullName, phone, newStatus, currentID).Scan(&p.ID, &p.AccountID, &p.Plane, &p.FullName, &p.PrimaryPhone, &p.Status, &p.CreatedAt, &p.UpdatedAt, &p.Version)
		if err != nil {
			return nil, err
		}
		_, err = tx.Exec(ctx, `
			INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
			VALUES ($1, $2, 'PROFILE', $3, 'CREATED', $4, $5)
		`, p.ID, actorAccountID, p.ID, p.Version, pq.StringArray([]string{"full_name", "status"}))
		if err != nil {
			return nil, err
		}
		if err := tx.Commit(ctx); err != nil {
			return nil, err
		}
		return &p, nil
	} else if !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}

	newID := uuid.New()
	newStatus := "INCOMPLETE"
	var fullName *string
	if req.FullName != nil && len(strings.TrimSpace(*req.FullName)) > 0 {
		t := strings.TrimSpace(*req.FullName)
		fullName = &t
		newStatus = "ACTIVE"
	}
	var phone *string
	if req.PrimaryPhone != nil && len(strings.TrimSpace(*req.PrimaryPhone)) > 0 {
		t := strings.TrimSpace(*req.PrimaryPhone)
		phone = &t
	}

	var p CustomerProfile
	err = tx.QueryRow(ctx, `
		INSERT INTO customer_profiles (id, account_id, plane, full_name, primary_phone, status, version)
		VALUES ($1, $2, 'app', $3, $4, $5, 1)
		RETURNING id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
	`, newID, accountID, fullName, phone, newStatus).Scan(
		&p.ID, &p.AccountID, &p.Plane, &p.FullName, &p.PrimaryPhone, &p.Status, &p.CreatedAt, &p.UpdatedAt, &p.Version,
	)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return nil, fmt.Errorf("%w: customer profile already exists", ErrConflict)
		}
		return nil, err
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'PROFILE', $3, 'CREATED', 1, $4)
	`, p.ID, actorAccountID, p.ID, pq.StringArray([]string{"id", "account_id"}))
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &p, nil
}

// UpdateProfile updates profile fields and logs audit evidence.
func (r *PgRepository) UpdateProfile(ctx context.Context, profileID uuid.UUID, req *UpdateProfileRequest, actorAccountID *uuid.UUID) (*CustomerProfile, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var current CustomerProfile
	err = tx.QueryRow(ctx, `
		SELECT id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
		FROM customer_profiles
		WHERE id = $1
		FOR UPDATE
	`, profileID).Scan(&current.ID, &current.AccountID, &current.Plane, &current.FullName, &current.PrimaryPhone, &current.Status, &current.CreatedAt, &current.UpdatedAt, &current.Version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}

	var changedFields []string
	newFullName := current.FullName
	if req.FullName != nil {
		trimmed := strings.TrimSpace(*req.FullName)
		newFullName = &trimmed
		changedFields = append(changedFields, "full_name")
	}

	newPhone := current.PrimaryPhone
	if req.PrimaryPhone != nil {
		trimmed := strings.TrimSpace(*req.PrimaryPhone)
		newPhone = &trimmed
		changedFields = append(changedFields, "primary_phone")
	}

	newStatus := current.Status
	if current.Status == "INCOMPLETE" && newFullName != nil && len(*newFullName) > 0 {
		newStatus = "ACTIVE"
		changedFields = append(changedFields, "status")
	}

	nextVersion := current.Version + 1
	var updated CustomerProfile
	err = tx.QueryRow(ctx, `
		UPDATE customer_profiles
		SET full_name = $1, primary_phone = $2, status = $3, version = $4, updated_at = now()
		WHERE id = $5
		RETURNING id, account_id, plane, full_name, primary_phone, status, created_at, updated_at, version
	`, newFullName, newPhone, newStatus, nextVersion, profileID).Scan(
		&updated.ID, &updated.AccountID, &updated.Plane, &updated.FullName, &updated.PrimaryPhone, &updated.Status, &updated.CreatedAt, &updated.UpdatedAt, &updated.Version,
	)
	if err != nil {
		return nil, err
	}

	if len(changedFields) > 0 {
		_, err = tx.Exec(ctx, `
			INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
			VALUES ($1, $2, 'PROFILE', $3, 'UPDATED', $4, $5)
		`, profileID, actorAccountID, profileID, nextVersion, pq.StringArray(changedFields))
		if err != nil {
			return nil, err
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &updated, nil
}

// CreateVehicle inserts a new customer vehicle.
func (r *PgRepository) CreateVehicle(ctx context.Context, customerID uuid.UUID, req *CreateVehicleRequest, actorAccountID *uuid.UUID) (*CustomerVehicle, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	newID := uuid.New()
	var regNum *string
	if req.RegistrationNumber != nil {
		u := strings.ToUpper(strings.TrimSpace(*req.RegistrationNumber))
		regNum = &u
	}

	connectors := req.ConnectorTypes
	if connectors == nil {
		connectors = []string{}
	}

	var v CustomerVehicle
	err = tx.QueryRow(ctx, `
		INSERT INTO customer_vehicles (
			id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, version
		) VALUES (
			$1, $2, $3, $4, $5, $6, $7,
			$8, $9, $10, $11, $12,
			$13, true, 1
		) RETURNING id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, created_at, updated_at, version
	`, newID, customerID, req.Label, strings.TrimSpace(req.Make), strings.TrimSpace(req.Model), req.Variant, regNum,
		req.BatteryCapacityWh, req.SupportsAC, req.SupportsDC, req.MaxACPowerW, req.MaxDCPowerW,
		pq.StringArray(connectors),
	).Scan(
		&v.ID, &v.CustomerID, &v.Label, &v.Make, &v.Model, &v.Variant, &v.RegistrationNumber,
		&v.BatteryCapacityWh, &v.SupportsAC, &v.SupportsDC, &v.MaxACPowerW, &v.MaxDCPowerW,
		(*pq.StringArray)(&v.ConnectorTypes), &v.IsActive, &v.CreatedAt, &v.UpdatedAt, &v.Version,
	)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return nil, fmt.Errorf("%w: active vehicle with this registration already exists", ErrConflict)
		}
		return nil, err
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'VEHICLE', $3, 'CREATED', 1, $4)
	`, customerID, actorAccountID, v.ID, pq.StringArray([]string{"id", "make", "model"}))
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &v, nil
}

// GetVehicleByID finds a vehicle by customer ID and vehicle ID.
func (r *PgRepository) GetVehicleByID(ctx context.Context, customerID, vehicleID uuid.UUID) (*CustomerVehicle, error) {
	row := r.Pool.QueryRow(ctx, `
		SELECT id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, created_at, updated_at, version
		FROM customer_vehicles
		WHERE id = $1 AND customer_id = $2
	`, vehicleID, customerID)

	var v CustomerVehicle
	err := row.Scan(
		&v.ID, &v.CustomerID, &v.Label, &v.Make, &v.Model, &v.Variant, &v.RegistrationNumber,
		&v.BatteryCapacityWh, &v.SupportsAC, &v.SupportsDC, &v.MaxACPowerW, &v.MaxDCPowerW,
		(*pq.StringArray)(&v.ConnectorTypes), &v.IsActive, &v.CreatedAt, &v.UpdatedAt, &v.Version,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &v, nil
}

// ListVehicles lists vehicles for a given customer.
func (r *PgRepository) ListVehicles(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]CustomerVehicle, error) {
	query := `
		SELECT id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, created_at, updated_at, version
		FROM customer_vehicles
		WHERE customer_id = $1
	`
	if activeOnly {
		query += " AND is_active = true"
	}
	query += " ORDER BY created_at DESC, id"

	rows, err := r.Pool.Query(ctx, query, customerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []CustomerVehicle
	for rows.Next() {
		var v CustomerVehicle
		if err := rows.Scan(
			&v.ID, &v.CustomerID, &v.Label, &v.Make, &v.Model, &v.Variant, &v.RegistrationNumber,
			&v.BatteryCapacityWh, &v.SupportsAC, &v.SupportsDC, &v.MaxACPowerW, &v.MaxDCPowerW,
			(*pq.StringArray)(&v.ConnectorTypes), &v.IsActive, &v.CreatedAt, &v.UpdatedAt, &v.Version,
		); err != nil {
			return nil, err
		}
		result = append(result, v)
	}
	return result, rows.Err()
}

// UpdateVehicle updates a customer vehicle.
func (r *PgRepository) UpdateVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, req *UpdateVehicleRequest, actorAccountID *uuid.UUID) (*CustomerVehicle, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var current CustomerVehicle
	err = tx.QueryRow(ctx, `
		SELECT id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, created_at, updated_at, version
		FROM customer_vehicles
		WHERE id = $1 AND customer_id = $2
		FOR UPDATE
	`, vehicleID, customerID).Scan(
		&current.ID, &current.CustomerID, &current.Label, &current.Make, &current.Model, &current.Variant, &current.RegistrationNumber,
		&current.BatteryCapacityWh, &current.SupportsAC, &current.SupportsDC, &current.MaxACPowerW, &current.MaxDCPowerW,
		(*pq.StringArray)(&current.ConnectorTypes), &current.IsActive, &current.CreatedAt, &current.UpdatedAt, &current.Version,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}

	if req.ExpectedVersion > 0 && current.Version != req.ExpectedVersion {
		return nil, ErrVersionMismatch
	}

	var changedFields []string
	if req.Label != nil {
		current.Label = req.Label
		changedFields = append(changedFields, "label")
	}
	if req.Make != nil {
		current.Make = strings.TrimSpace(*req.Make)
		changedFields = append(changedFields, "make")
	}
	if req.Model != nil {
		current.Model = strings.TrimSpace(*req.Model)
		changedFields = append(changedFields, "model")
	}
	if req.Variant != nil {
		current.Variant = req.Variant
		changedFields = append(changedFields, "variant")
	}
	if req.RegistrationNumber != nil {
		u := strings.ToUpper(strings.TrimSpace(*req.RegistrationNumber))
		current.RegistrationNumber = &u
		changedFields = append(changedFields, "registration_number")
	}
	if req.BatteryCapacityWh != nil {
		current.BatteryCapacityWh = req.BatteryCapacityWh
		changedFields = append(changedFields, "battery_capacity_wh")
	}
	if req.SupportsAC != nil {
		current.SupportsAC = req.SupportsAC
		changedFields = append(changedFields, "supports_ac")
	}
	if req.SupportsDC != nil {
		current.SupportsDC = req.SupportsDC
		changedFields = append(changedFields, "supports_dc")
	}
	if req.MaxACPowerW != nil {
		current.MaxACPowerW = req.MaxACPowerW
		changedFields = append(changedFields, "max_ac_power_w")
	}
	if req.MaxDCPowerW != nil {
		current.MaxDCPowerW = req.MaxDCPowerW
		changedFields = append(changedFields, "max_dc_power_w")
	}
	if req.ConnectorTypes != nil {
		current.ConnectorTypes = req.ConnectorTypes
		changedFields = append(changedFields, "connector_types")
	}
	if req.IsActive != nil {
		current.IsActive = *req.IsActive
		changedFields = append(changedFields, "is_active")
	}

	nextVersion := current.Version + 1
	var updated CustomerVehicle
	err = tx.QueryRow(ctx, `
		UPDATE customer_vehicles
		SET label = $1, make = $2, model = $3, variant = $4, registration_number = $5,
			battery_capacity_wh = $6, supports_ac = $7, supports_dc = $8, max_ac_power_w = $9, max_dc_power_w = $10,
			connector_types = $11, is_active = $12, version = $13, updated_at = now()
		WHERE id = $14 AND customer_id = $15
		RETURNING id, customer_id, label, make, model, variant, registration_number,
			battery_capacity_wh, supports_ac, supports_dc, max_ac_power_w, max_dc_power_w,
			connector_types, is_active, created_at, updated_at, version
	`, current.Label, current.Make, current.Model, current.Variant, current.RegistrationNumber,
		current.BatteryCapacityWh, current.SupportsAC, current.SupportsDC, current.MaxACPowerW, current.MaxDCPowerW,
		pq.StringArray(current.ConnectorTypes), current.IsActive, nextVersion, vehicleID, customerID,
	).Scan(
		&updated.ID, &updated.CustomerID, &updated.Label, &updated.Make, &updated.Model, &updated.Variant, &updated.RegistrationNumber,
		&updated.BatteryCapacityWh, &updated.SupportsAC, &updated.SupportsDC, &updated.MaxACPowerW, &updated.MaxDCPowerW,
		(*pq.StringArray)(&updated.ConnectorTypes), &updated.IsActive, &updated.CreatedAt, &updated.UpdatedAt, &updated.Version,
	)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return nil, fmt.Errorf("%w: active vehicle with this registration already exists", ErrConflict)
		}
		return nil, err
	}

	if len(changedFields) > 0 {
		_, err = tx.Exec(ctx, `
			INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
			VALUES ($1, $2, 'VEHICLE', $3, 'UPDATED', $4, $5)
		`, customerID, actorAccountID, vehicleID, nextVersion, pq.StringArray(changedFields))
		if err != nil {
			return nil, err
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &updated, nil
}

// DeleteVehicle deactivates a customer vehicle (soft delete preserving references).
func (r *PgRepository) DeleteVehicle(ctx context.Context, customerID, vehicleID uuid.UUID, actorAccountID *uuid.UUID) error {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentVersion int64
	err = tx.QueryRow(ctx, `
		SELECT version FROM customer_vehicles
		WHERE id = $1 AND customer_id = $2 AND is_active = true
		FOR UPDATE
	`, vehicleID, customerID).Scan(&currentVersion)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}

	nextVersion := currentVersion + 1
	_, err = tx.Exec(ctx, `
		UPDATE customer_vehicles
		SET is_active = false, version = $1, updated_at = now()
		WHERE id = $2 AND customer_id = $3
	`, nextVersion, vehicleID, customerID)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'VEHICLE', $3, 'DEACTIVATED', $4, $5)
	`, customerID, actorAccountID, vehicleID, nextVersion, pq.StringArray([]string{"is_active"}))
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

// CreateAddress creates a new customer address, maintaining the single active default constraint.
func (r *PgRepository) CreateAddress(ctx context.Context, customerID uuid.UUID, req *CreateAddressRequest, actorAccountID *uuid.UUID) (*CustomerAddress, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	if req.IsDefault {
		_, err = tx.Exec(ctx, `
			UPDATE customer_addresses
			SET is_default = false, updated_at = now()
			WHERE customer_id = $1 AND is_active = true AND is_default = true
		`, customerID)
		if err != nil {
			return nil, err
		}
	}

	newID := uuid.New()
	countryCode := "IN"
	if req.CountryCode != nil {
		countryCode = strings.ToUpper(strings.TrimSpace(*req.CountryCode))
	}

	var latVal, lonVal *decimal.Decimal
	if req.Latitude != nil && req.Longitude != nil {
		dLat, err := decimal.NewFromString(*req.Latitude)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid latitude", ErrInvalid)
		}
		dLon, err := decimal.NewFromString(*req.Longitude)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid longitude", ErrInvalid)
		}
		latVal = &dLat
		lonVal = &dLon
	}

	var addr CustomerAddress
	var latRes, lonRes *decimal.Decimal
	err = tx.QueryRow(ctx, `
		INSERT INTO customer_addresses (
			id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, version
		) VALUES (
			$1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
			$11, $12, $13, $14, $15, $16,
			$17, $18, $19, true, 1
		) RETURNING id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, created_at, updated_at, version
	`, newID, customerID, req.Label, strings.TrimSpace(req.Line1), req.Line2, req.Locality, strings.TrimSpace(req.City), strings.TrimSpace(req.State), req.PostalCode, countryCode,
		latVal, lonVal, req.Landmark, req.BuildingInstructions, req.Floor, req.ContactInstructions,
		req.GeocodeProvider, req.GeocodeExternalRef, req.IsDefault,
	).Scan(
		&addr.ID, &addr.CustomerID, &addr.Label, &addr.Line1, &addr.Line2, &addr.Locality, &addr.City, &addr.State, &addr.PostalCode, &addr.CountryCode,
		&latRes, &lonRes, &addr.Landmark, &addr.BuildingInstructions, &addr.Floor, &addr.ContactInstructions,
		&addr.GeocodeProvider, &addr.GeocodeExternalRef, &addr.IsDefault, &addr.IsActive, &addr.CreatedAt, &addr.UpdatedAt, &addr.Version,
	)
	if err != nil {
		return nil, err
	}

	if latRes != nil {
		s := latRes.String()
		addr.Latitude = &s
	}
	if lonRes != nil {
		s := lonRes.String()
		addr.Longitude = &s
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'ADDRESS', $3, 'CREATED', 1, $4)
	`, customerID, actorAccountID, addr.ID, pq.StringArray([]string{"id", "line1", "city", "state"}))
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &addr, nil
}

// GetAddressByID fetches an address by id and customer id.
func (r *PgRepository) GetAddressByID(ctx context.Context, customerID, addressID uuid.UUID) (*CustomerAddress, error) {
	var addr CustomerAddress
	var latRes, lonRes *decimal.Decimal
	row := r.Pool.QueryRow(ctx, `
		SELECT id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, created_at, updated_at, version
		FROM customer_addresses
		WHERE id = $1 AND customer_id = $2
	`, addressID, customerID)

	err := row.Scan(
		&addr.ID, &addr.CustomerID, &addr.Label, &addr.Line1, &addr.Line2, &addr.Locality, &addr.City, &addr.State, &addr.PostalCode, &addr.CountryCode,
		&latRes, &lonRes, &addr.Landmark, &addr.BuildingInstructions, &addr.Floor, &addr.ContactInstructions,
		&addr.GeocodeProvider, &addr.GeocodeExternalRef, &addr.IsDefault, &addr.IsActive, &addr.CreatedAt, &addr.UpdatedAt, &addr.Version,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if latRes != nil {
		s := latRes.String()
		addr.Latitude = &s
	}
	if lonRes != nil {
		s := lonRes.String()
		addr.Longitude = &s
	}
	return &addr, nil
}

// ListAddresses lists addresses for a customer.
func (r *PgRepository) ListAddresses(ctx context.Context, customerID uuid.UUID, activeOnly bool) ([]CustomerAddress, error) {
	query := `
		SELECT id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, created_at, updated_at, version
		FROM customer_addresses
		WHERE customer_id = $1
	`
	if activeOnly {
		query += " AND is_active = true"
	}
	query += " ORDER BY is_default DESC, created_at DESC, id"

	rows, err := r.Pool.Query(ctx, query, customerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var result []CustomerAddress
	for rows.Next() {
		var addr CustomerAddress
		var latRes, lonRes *decimal.Decimal
		if err := rows.Scan(
			&addr.ID, &addr.CustomerID, &addr.Label, &addr.Line1, &addr.Line2, &addr.Locality, &addr.City, &addr.State, &addr.PostalCode, &addr.CountryCode,
			&latRes, &lonRes, &addr.Landmark, &addr.BuildingInstructions, &addr.Floor, &addr.ContactInstructions,
			&addr.GeocodeProvider, &addr.GeocodeExternalRef, &addr.IsDefault, &addr.IsActive, &addr.CreatedAt, &addr.UpdatedAt, &addr.Version,
		); err != nil {
			return nil, err
		}
		if latRes != nil {
			s := latRes.String()
			addr.Latitude = &s
		}
		if lonRes != nil {
			s := lonRes.String()
			addr.Longitude = &s
		}
		result = append(result, addr)
	}
	return result, rows.Err()
}

// UpdateAddress updates a customer address.
func (r *PgRepository) UpdateAddress(ctx context.Context, customerID, addressID uuid.UUID, req *UpdateAddressRequest, actorAccountID *uuid.UUID) (*CustomerAddress, error) {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	var current CustomerAddress
	var latCur, lonCur *decimal.Decimal
	err = tx.QueryRow(ctx, `
		SELECT id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, created_at, updated_at, version
		FROM customer_addresses
		WHERE id = $1 AND customer_id = $2
		FOR UPDATE
	`, addressID, customerID).Scan(
		&current.ID, &current.CustomerID, &current.Label, &current.Line1, &current.Line2, &current.Locality, &current.City, &current.State, &current.PostalCode, &current.CountryCode,
		&latCur, &lonCur, &current.Landmark, &current.BuildingInstructions, &current.Floor, &current.ContactInstructions,
		&current.GeocodeProvider, &current.GeocodeExternalRef, &current.IsDefault, &current.IsActive, &current.CreatedAt, &current.UpdatedAt, &current.Version,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}

	if req.ExpectedVersion > 0 && current.Version != req.ExpectedVersion {
		return nil, ErrVersionMismatch
	}

	var changedFields []string
	if req.Label != nil {
		current.Label = req.Label
		changedFields = append(changedFields, "label")
	}
	if req.Line1 != nil {
		current.Line1 = strings.TrimSpace(*req.Line1)
		changedFields = append(changedFields, "line1")
	}
	if req.Line2 != nil {
		current.Line2 = req.Line2
		changedFields = append(changedFields, "line2")
	}
	if req.Locality != nil {
		current.Locality = req.Locality
		changedFields = append(changedFields, "locality")
	}
	if req.City != nil {
		current.City = strings.TrimSpace(*req.City)
		changedFields = append(changedFields, "city")
	}
	if req.State != nil {
		current.State = strings.TrimSpace(*req.State)
		changedFields = append(changedFields, "state")
	}
	if req.PostalCode != nil {
		current.PostalCode = req.PostalCode
		changedFields = append(changedFields, "postal_code")
	}
	if req.CountryCode != nil {
		current.CountryCode = strings.ToUpper(strings.TrimSpace(*req.CountryCode))
		changedFields = append(changedFields, "country_code")
	}
	if req.Latitude != nil && req.Longitude != nil {
		dLat, err := decimal.NewFromString(*req.Latitude)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid latitude", ErrInvalid)
		}
		dLon, err := decimal.NewFromString(*req.Longitude)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid longitude", ErrInvalid)
		}
		latCur = &dLat
		lonCur = &dLon
		changedFields = append(changedFields, "latitude", "longitude")
	}
	if req.Landmark != nil {
		current.Landmark = req.Landmark
		changedFields = append(changedFields, "landmark")
	}
	if req.BuildingInstructions != nil {
		current.BuildingInstructions = req.BuildingInstructions
		changedFields = append(changedFields, "building_instructions")
	}
	if req.Floor != nil {
		current.Floor = req.Floor
		changedFields = append(changedFields, "floor")
	}
	if req.ContactInstructions != nil {
		current.ContactInstructions = req.ContactInstructions
		changedFields = append(changedFields, "contact_instructions")
	}
	if req.GeocodeProvider != nil {
		current.GeocodeProvider = req.GeocodeProvider
		changedFields = append(changedFields, "geocode_provider")
	}
	if req.GeocodeExternalRef != nil {
		current.GeocodeExternalRef = req.GeocodeExternalRef
		changedFields = append(changedFields, "geocode_external_ref")
	}
	if req.IsActive != nil {
		current.IsActive = *req.IsActive
		changedFields = append(changedFields, "is_active")
	}
	if req.IsDefault != nil && *req.IsDefault {
		_, err = tx.Exec(ctx, `
			UPDATE customer_addresses
			SET is_default = false, updated_at = now()
			WHERE customer_id = $1 AND is_active = true AND is_default = true AND id != $2
		`, customerID, addressID)
		if err != nil {
			return nil, err
		}
		current.IsDefault = true
		changedFields = append(changedFields, "is_default")
	} else if req.IsDefault != nil {
		current.IsDefault = *req.IsDefault
		changedFields = append(changedFields, "is_default")
	}

	nextVersion := current.Version + 1
	var updated CustomerAddress
	var latRes, lonRes *decimal.Decimal
	err = tx.QueryRow(ctx, `
		UPDATE customer_addresses
		SET label = $1, line1 = $2, line2 = $3, locality = $4, city = $5, state = $6,
			postal_code = $7, country_code = $8, latitude = $9, longitude = $10,
			landmark = $11, building_instructions = $12, floor = $13, contact_instructions = $14,
			geocode_provider = $15, geocode_external_ref = $16, is_default = $17, is_active = $18,
			version = $19, updated_at = now()
		WHERE id = $20 AND customer_id = $21
		RETURNING id, customer_id, label, line1, line2, locality, city, state, postal_code, country_code,
			latitude, longitude, landmark, building_instructions, floor, contact_instructions,
			geocode_provider, geocode_external_ref, is_default, is_active, created_at, updated_at, version
	`, current.Label, current.Line1, current.Line2, current.Locality, current.City, current.State,
		current.PostalCode, current.CountryCode, latCur, lonCur,
		current.Landmark, current.BuildingInstructions, current.Floor, current.ContactInstructions,
		current.GeocodeProvider, current.GeocodeExternalRef, current.IsDefault, current.IsActive,
		nextVersion, addressID, customerID,
	).Scan(
		&updated.ID, &updated.CustomerID, &updated.Label, &updated.Line1, &updated.Line2, &updated.Locality, &updated.City, &updated.State, &updated.PostalCode, &updated.CountryCode,
		&latRes, &lonRes, &updated.Landmark, &updated.BuildingInstructions, &updated.Floor, &updated.ContactInstructions,
		&updated.GeocodeProvider, &updated.GeocodeExternalRef, &updated.IsDefault, &updated.IsActive, &updated.CreatedAt, &updated.UpdatedAt, &updated.Version,
	)
	if err != nil {
		return nil, err
	}

	if latRes != nil {
		s := latRes.String()
		updated.Latitude = &s
	}
	if lonRes != nil {
		s := lonRes.String()
		updated.Longitude = &s
	}

	if len(changedFields) > 0 {
		_, err = tx.Exec(ctx, `
			INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
			VALUES ($1, $2, 'ADDRESS', $3, 'UPDATED', $4, $5)
		`, customerID, actorAccountID, addressID, nextVersion, pq.StringArray(changedFields))
		if err != nil {
			return nil, err
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &updated, nil
}

// DeleteAddress deactivates an address.
func (r *PgRepository) DeleteAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentVersion int64
	err = tx.QueryRow(ctx, `
		SELECT version FROM customer_addresses
		WHERE id = $1 AND customer_id = $2 AND is_active = true
		FOR UPDATE
	`, addressID, customerID).Scan(&currentVersion)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}

	nextVersion := currentVersion + 1
	_, err = tx.Exec(ctx, `
		UPDATE customer_addresses
		SET is_active = false, is_default = false, version = $1, updated_at = now()
		WHERE id = $2 AND customer_id = $3
	`, nextVersion, addressID, customerID)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'ADDRESS', $3, 'DEACTIVATED', $4, $5)
	`, customerID, actorAccountID, addressID, nextVersion, pq.StringArray([]string{"is_active", "is_default"}))
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}

// SetDefaultAddress switches an address to be the sole active default.
func (r *PgRepository) SetDefaultAddress(ctx context.Context, customerID, addressID uuid.UUID, actorAccountID *uuid.UUID) error {
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var currentVersion int64
	var isActive bool
	err = tx.QueryRow(ctx, `
		SELECT version, is_active FROM customer_addresses
		WHERE id = $1 AND customer_id = $2
		FOR UPDATE
	`, addressID, customerID).Scan(&currentVersion, &isActive)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if !isActive {
		return fmt.Errorf("%w: cannot set an inactive address as default", ErrConflict)
	}

	_, err = tx.Exec(ctx, `
		UPDATE customer_addresses
		SET is_default = false, updated_at = now()
		WHERE customer_id = $1 AND is_active = true AND is_default = true AND id != $2
	`, customerID, addressID)
	if err != nil {
		return err
	}

	nextVersion := currentVersion + 1
	_, err = tx.Exec(ctx, `
		UPDATE customer_addresses
		SET is_default = true, version = $1, updated_at = now()
		WHERE id = $2 AND customer_id = $3
	`, nextVersion, addressID, customerID)
	if err != nil {
		return err
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO customer_master_audit (customer_id, actor_account_id, resource_kind, resource_id, action, resulting_version, changed_fields)
		VALUES ($1, $2, 'ADDRESS', $3, 'SET_DEFAULT', $4, $5)
	`, customerID, actorAccountID, addressID, nextVersion, pq.StringArray([]string{"is_default"}))
	if err != nil {
		return err
	}

	return tx.Commit(ctx)
}
