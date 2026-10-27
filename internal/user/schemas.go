package user

import (
	"errors"
	"strings"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/models"
)

var (
	ErrNotFound      = errors.New("vehicle not found")
	ErrConflict      = errors.New("vehicle with this registration number is already registered")
	ErrInvalidInput  = errors.New("invalid vehicle data")
	ErrUnauthorized  = errors.New("unauthorized")
	ErrDependency    = errors.New("service temporarily unavailable")
)

// CreateVehicleRequest defines parameters for registering a new customer vehicle.
type CreateVehicleRequest struct {
	Name               *string `json:"name,omitempty"`
	VehicleType        string  `json:"vehicle_type"`
	Make               *string `json:"make,omitempty"`
	Model              *string `json:"model,omitempty"`
	RegistrationNumber string  `json:"registration_number"`
	BatteryCapacityKWh *float64 `json:"battery_capacity_kwh,omitempty"`
	IsDefault          bool    `json:"is_default"`
}

// UpdateVehicleRequest defines mutable fields for an existing customer vehicle.
type UpdateVehicleRequest struct {
	Name               *string  `json:"name,omitempty"`
	VehicleType        *string  `json:"vehicle_type,omitempty"`
	Make               *string  `json:"make,omitempty"`
	Model              *string  `json:"model,omitempty"`
	RegistrationNumber *string  `json:"registration_number,omitempty"`
	BatteryCapacityKWh *float64 `json:"battery_capacity_kwh,omitempty"`
	IsDefault          *bool    `json:"is_default,omitempty"`
}

// VehicleResponse represents the API serialization of a registered customer vehicle.
type VehicleResponse struct {
	ID                 string     `json:"id"`
	CustomerAccountID  string     `json:"customer_account_id"`
	Name               *string    `json:"name,omitempty"`
	VehicleType        string     `json:"vehicle_type"`
	Make               *string    `json:"make,omitempty"`
	Model              *string    `json:"model,omitempty"`
	RegistrationNumber string     `json:"registration_number"`
	BatteryCapacityKWh *float64   `json:"battery_capacity_kwh,omitempty"`
	IsDefault          bool       `json:"is_default"`
	LastCharged        *time.Time `json:"last_charged,omitempty"`
	CreatedAt          time.Time  `json:"created_at"`
	UpdatedAt          time.Time  `json:"updated_at"`
}

// IsValidVehicleType checks if the vehicle type string matches recognized customer vehicle types.
func IsValidVehicleType(t string) bool {
	switch models.CustomerVehicleType(strings.ToUpper(strings.TrimSpace(t))) {
	case models.VehicleCar,
		models.VehicleMotorcycle,
		models.VehicleScooter,
		models.VehicleThreeWheeler,
		models.VehicleOther:
		return true
	default:
		return false
	}
}

// Validate validates the CreateVehicleRequest payload.
func (r *CreateVehicleRequest) Validate() error {
	r.RegistrationNumber = strings.ToUpper(strings.TrimSpace(r.RegistrationNumber))
	if len(r.RegistrationNumber) < 2 || len(r.RegistrationNumber) > 50 {
		return errors.New("registration_number must be between 2 and 50 characters")
	}

	r.VehicleType = strings.ToUpper(strings.TrimSpace(r.VehicleType))
	if r.VehicleType == "" {
		r.VehicleType = string(models.VehicleCar)
	} else if !IsValidVehicleType(r.VehicleType) {
		return errors.New("invalid vehicle_type; allowed: CAR, MOTORCYCLE, SCOOTER, THREE_WHEELER, OTHER")
	}

	if r.BatteryCapacityKWh != nil && *r.BatteryCapacityKWh <= 0 {
		return errors.New("battery_capacity_kwh must be greater than 0")
	}

	if r.Name != nil {
		trimmed := strings.TrimSpace(*r.Name)
		if len(trimmed) > 100 {
			return errors.New("name must not exceed 100 characters")
		}
		if trimmed == "" {
			r.Name = nil
		} else {
			r.Name = &trimmed
		}
	}

	if r.Make != nil {
		trimmed := strings.TrimSpace(*r.Make)
		if len(trimmed) > 100 {
			return errors.New("make must not exceed 100 characters")
		}
		if trimmed == "" {
			r.Make = nil
		} else {
			r.Make = &trimmed
		}
	}

	if r.Model != nil {
		trimmed := strings.TrimSpace(*r.Model)
		if len(trimmed) > 100 {
			return errors.New("model must not exceed 100 characters")
		}
		if trimmed == "" {
			r.Model = nil
		} else {
			r.Model = &trimmed
		}
	}

	return nil
}

// Validate validates the UpdateVehicleRequest payload.
func (r *UpdateVehicleRequest) Validate() error {
	hasUpdate := false

	if r.RegistrationNumber != nil {
		trimmed := strings.ToUpper(strings.TrimSpace(*r.RegistrationNumber))
		if len(trimmed) < 2 || len(trimmed) > 50 {
			return errors.New("registration_number must be between 2 and 50 characters")
		}
		r.RegistrationNumber = &trimmed
		hasUpdate = true
	}

	if r.VehicleType != nil {
		upper := strings.ToUpper(strings.TrimSpace(*r.VehicleType))
		if !IsValidVehicleType(upper) {
			return errors.New("invalid vehicle_type; allowed: CAR, MOTORCYCLE, SCOOTER, THREE_WHEELER, OTHER")
		}
		r.VehicleType = &upper
		hasUpdate = true
	}

	if r.BatteryCapacityKWh != nil {
		if *r.BatteryCapacityKWh <= 0 {
			return errors.New("battery_capacity_kwh must be greater than 0")
		}
		hasUpdate = true
	}

	if r.Name != nil {
		trimmed := strings.TrimSpace(*r.Name)
		if len(trimmed) > 100 {
			return errors.New("name must not exceed 100 characters")
		}
		r.Name = &trimmed
		hasUpdate = true
	}

	if r.Make != nil {
		trimmed := strings.TrimSpace(*r.Make)
		if len(trimmed) > 100 {
			return errors.New("make must not exceed 100 characters")
		}
		r.Make = &trimmed
		hasUpdate = true
	}

	if r.Model != nil {
		trimmed := strings.TrimSpace(*r.Model)
		if len(trimmed) > 100 {
			return errors.New("model must not exceed 100 characters")
		}
		r.Model = &trimmed
		hasUpdate = true
	}

	if r.IsDefault != nil {
		hasUpdate = true
	}

	if !hasUpdate {
		return errors.New("at least one field must be provided for update")
	}

	return nil
}

