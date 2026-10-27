package user

import (
	"context"
	"errors"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/security"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Repository defines the persistence interface for customer vehicle operations.
type Repository interface {
	Create(ctx context.Context, accountID string, req CreateVehicleRequest) (*VehicleResponse, error)
	GetByID(ctx context.Context, accountID, vehicleID string) (*VehicleResponse, error)
	ListByAccount(ctx context.Context, accountID string) ([]VehicleResponse, error)
	Update(ctx context.Context, accountID, vehicleID string, req UpdateVehicleRequest) (*VehicleResponse, error)
	Delete(ctx context.Context, accountID, vehicleID string) error
	SetDefault(ctx context.Context, accountID, vehicleID string) error
}

type pgxRepository struct {
	pool *pgxpool.Pool
}

// NewRepository creates a new vehicle repository using the provided pgx connection pool.
func NewRepository(pool *pgxpool.Pool) Repository {
	return &pgxRepository{pool: pool}
}

func (r *pgxRepository) Create(ctx context.Context, accountID string, req CreateVehicleRequest) (*VehicleResponse, error) {
	id, err := security.UUID()
	if err != nil {
		return nil, ErrDependency
	}

	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return nil, ErrDependency
	}
	defer tx.Rollback(ctx)

	// Check if this is the customer's first vehicle. If so, make it the default.
	var count int
	if err = tx.QueryRow(ctx, `SELECT count(*) FROM customer_vehicles WHERE customer_account_id = $1`, accountID).Scan(&count); err != nil {
		return nil, ErrDependency
	}

	isDefault := req.IsDefault || count == 0
	if isDefault && count > 0 {
		if _, err = tx.Exec(ctx, `UPDATE customer_vehicles SET is_default = false WHERE customer_account_id = $1`, accountID); err != nil {
			return nil, ErrDependency
		}
	}

	var res VehicleResponse
	err = tx.QueryRow(ctx, `
		INSERT INTO customer_vehicles (
			id, customer_account_id, name, vehicle_type, make, model,
			registration_number, battery_capacity_kwh, is_default
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		RETURNING id, customer_account_id, name, vehicle_type, make, model,
		          registration_number, battery_capacity_kwh, is_default, last_charged,
		          created_at, updated_at
	`, id, accountID, req.Name, req.VehicleType, req.Make, req.Model,
		req.RegistrationNumber, req.BatteryCapacityKWh, isDefault).Scan(
		&res.ID, &res.CustomerAccountID, &res.Name, &res.VehicleType, &res.Make, &res.Model,
		&res.RegistrationNumber, &res.BatteryCapacityKWh, &res.IsDefault, &res.LastCharged,
		&res.CreatedAt, &res.UpdatedAt,
	)

	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return nil, ErrConflict
		}
		return nil, ErrDependency
	}

	if err = tx.Commit(ctx); err != nil {
		return nil, ErrDependency
	}

	return &res, nil
}

func (r *pgxRepository) GetByID(ctx context.Context, accountID, vehicleID string) (*VehicleResponse, error) {
	var res VehicleResponse
	err := r.pool.QueryRow(ctx, `
		SELECT id, customer_account_id, name, vehicle_type, make, model,
		       registration_number, battery_capacity_kwh, is_default, last_charged,
		       created_at, updated_at
		FROM customer_vehicles
		WHERE id = $1 AND customer_account_id = $2
	`, vehicleID, accountID).Scan(
		&res.ID, &res.CustomerAccountID, &res.Name, &res.VehicleType, &res.Make, &res.Model,
		&res.RegistrationNumber, &res.BatteryCapacityKWh, &res.IsDefault, &res.LastCharged,
		&res.CreatedAt, &res.UpdatedAt,
	)

	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, ErrDependency
	}

	return &res, nil
}

func (r *pgxRepository) ListByAccount(ctx context.Context, accountID string) ([]VehicleResponse, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, customer_account_id, name, vehicle_type, make, model,
		       registration_number, battery_capacity_kwh, is_default, last_charged,
		       created_at, updated_at
		FROM customer_vehicles
		WHERE customer_account_id = $1
		ORDER BY is_default DESC, created_at DESC
	`, accountID)
	if err != nil {
		return nil, ErrDependency
	}
	defer rows.Close()

	var list []VehicleResponse
	for rows.Next() {
		var item VehicleResponse
		if err := rows.Scan(
			&item.ID, &item.CustomerAccountID, &item.Name, &item.VehicleType, &item.Make, &item.Model,
			&item.RegistrationNumber, &item.BatteryCapacityKWh, &item.IsDefault, &item.LastCharged,
			&item.CreatedAt, &item.UpdatedAt,
		); err != nil {
			return nil, ErrDependency
		}
		list = append(list, item)
	}

	if err = rows.Err(); err != nil {
		return nil, ErrDependency
	}

	if list == nil {
		list = []VehicleResponse{}
	}

	return list, nil
}

func (r *pgxRepository) Update(ctx context.Context, accountID, vehicleID string, req UpdateVehicleRequest) (*VehicleResponse, error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return nil, ErrDependency
	}
	defer tx.Rollback(ctx)

	// Verify vehicle exists and belongs to customer.
	var currentIsDefault bool
	err = tx.QueryRow(ctx, `
		SELECT is_default FROM customer_vehicles
		WHERE id = $1 AND customer_account_id = $2
		FOR UPDATE
	`, vehicleID, accountID).Scan(&currentIsDefault)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, ErrDependency
	}

	if req.IsDefault != nil && *req.IsDefault && !currentIsDefault {
		if _, err = tx.Exec(ctx, `UPDATE customer_vehicles SET is_default = false WHERE customer_account_id = $1 AND id <> $2`, accountID, vehicleID); err != nil {
			return nil, ErrDependency
		}
	}

	var res VehicleResponse
	err = tx.QueryRow(ctx, `
		UPDATE customer_vehicles SET
			name = COALESCE($3, name),
			vehicle_type = COALESCE($4, vehicle_type),
			make = COALESCE($5, make),
			model = COALESCE($6, model),
			registration_number = COALESCE($7, registration_number),
			battery_capacity_kwh = COALESCE($8, battery_capacity_kwh),
			is_default = COALESCE($9, is_default),
			updated_at = now()
		WHERE id = $1 AND customer_account_id = $2
		RETURNING id, customer_account_id, name, vehicle_type, make, model,
		          registration_number, battery_capacity_kwh, is_default, last_charged,
		          created_at, updated_at
	`, vehicleID, accountID, req.Name, req.VehicleType, req.Make, req.Model,
		req.RegistrationNumber, req.BatteryCapacityKWh, req.IsDefault).Scan(
		&res.ID, &res.CustomerAccountID, &res.Name, &res.VehicleType, &res.Make, &res.Model,
		&res.RegistrationNumber, &res.BatteryCapacityKWh, &res.IsDefault, &res.LastCharged,
		&res.CreatedAt, &res.UpdatedAt,
	)

	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == "23505" {
			return nil, ErrConflict
		}
		return nil, ErrDependency
	}

	if err = tx.Commit(ctx); err != nil {
		return nil, ErrDependency
	}

	return &res, nil
}

func (r *pgxRepository) Delete(ctx context.Context, accountID, vehicleID string) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)

	var wasDefault bool
	err = tx.QueryRow(ctx, `SELECT is_default FROM customer_vehicles WHERE id = $1 AND customer_account_id = $2 FOR UPDATE`, vehicleID, accountID).Scan(&wasDefault)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return ErrDependency
	}

	cmdTag, err := tx.Exec(ctx, `DELETE FROM customer_vehicles WHERE id = $1 AND customer_account_id = $2`, vehicleID, accountID)
	if err != nil {
		return ErrDependency
	}
	if cmdTag.RowsAffected() == 0 {
		return ErrNotFound
	}

	// If the deleted vehicle was the default, automatically assign default to the oldest remaining vehicle.
	if wasDefault {
		_, _ = tx.Exec(ctx, `
			UPDATE customer_vehicles SET is_default = true, updated_at = now()
			WHERE id = (
				SELECT id FROM customer_vehicles
				WHERE customer_account_id = $1
				ORDER BY created_at ASC
				LIMIT 1
			)
		`, accountID)
	}

	return tx.Commit(ctx)
}

func (r *pgxRepository) SetDefault(ctx context.Context, accountID, vehicleID string) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return ErrDependency
	}
	defer tx.Rollback(ctx)

	var exists bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM customer_vehicles WHERE id = $1 AND customer_account_id = $2)`, vehicleID, accountID).Scan(&exists)
	if err != nil {
		return ErrDependency
	}
	if !exists {
		return ErrNotFound
	}

	if _, err = tx.Exec(ctx, `UPDATE customer_vehicles SET is_default = false WHERE customer_account_id = $1`, accountID); err != nil {
		return ErrDependency
	}

	if _, err = tx.Exec(ctx, `UPDATE customer_vehicles SET is_default = true, updated_at = now() WHERE id = $1 AND customer_account_id = $2`, vehicleID, accountID); err != nil {
		return ErrDependency
	}

	return tx.Commit(ctx)
}
