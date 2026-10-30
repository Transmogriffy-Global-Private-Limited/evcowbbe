// Package customer owns the customer profile, saved vehicles, and addresses.
// It reflects the customer foundation schema (migration 004) and Ent customer entities.
package customer

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/shopspring/decimal"
)

var (
	ErrNotFound        = errors.New("customer resource not found")
	ErrInvalid         = errors.New("customer request validation failed")
	ErrConflict        = errors.New("customer state conflict")
	ErrVersionMismatch = errors.New("customer resource version mismatch")
	ErrUnauthorized    = errors.New("unauthorized customer access")
	ErrForbidden       = errors.New("customer plane forbidden")
)

var (
	countryCodeRe = regexp.MustCompile(`^[A-Z]{2}$`)
	regNumberRe   = regexp.MustCompile(`^[A-Z0-9\-\s]{2,32}$`)
)

// CustomerProfile represents the customer's identity in the app plane.
type CustomerProfile struct {
	ID           uuid.UUID `json:"id"`
	AccountID    uuid.UUID `json:"account_id"`
	Plane        string    `json:"plane"`
	FullName     *string   `json:"full_name,omitempty"`
	PrimaryPhone *string   `json:"primary_phone,omitempty"`
	Status       string    `json:"status"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
	Version      int64     `json:"version"`
}

// CreateProfileRequest captures initial profile registration.
type CreateProfileRequest struct {
	FullName     *string `json:"full_name"`
	PrimaryPhone *string `json:"primary_phone"`
}

// Validate validates CreateProfileRequest.
func (r *CreateProfileRequest) Validate() error {
	if r.FullName != nil {
		trimmed := strings.TrimSpace(*r.FullName)
		if len(trimmed) < 1 || len(trimmed) > 160 {
			return fmt.Errorf("%w: full_name must be between 1 and 160 characters", ErrInvalid)
		}
	}
	if r.PrimaryPhone != nil {
		trimmed := strings.TrimSpace(*r.PrimaryPhone)
		if len(trimmed) < 7 || len(trimmed) > 32 {
			return fmt.Errorf("%w: primary_phone must be between 7 and 32 characters", ErrInvalid)
		}
	}
	return nil
}

// UpdateProfileRequest captures mutable profile attributes for PATCH/PUT.
type UpdateProfileRequest struct {
	FullName     *string `json:"full_name"`
	PrimaryPhone *string `json:"primary_phone"`
}

// Validate validates UpdateProfileRequest.
func (r *UpdateProfileRequest) Validate() error {
	if r.FullName != nil {
		trimmed := strings.TrimSpace(*r.FullName)
		if len(trimmed) < 1 || len(trimmed) > 160 {
			return fmt.Errorf("%w: full_name must be between 1 and 160 characters", ErrInvalid)
		}
	}
	if r.PrimaryPhone != nil {
		trimmed := strings.TrimSpace(*r.PrimaryPhone)
		if len(trimmed) < 7 || len(trimmed) > 32 {
			return fmt.Errorf("%w: primary_phone must be between 7 and 32 characters", ErrInvalid)
		}
	}
	return nil
}

// CustomerVehicle describes a user-owned vehicle registered for charging.
type CustomerVehicle struct {
	ID                 uuid.UUID `json:"id"`
	CustomerID         uuid.UUID `json:"customer_id"`
	Label              *string   `json:"label,omitempty"`
	Make               string    `json:"make"`
	Model              string    `json:"model"`
	Variant            *string   `json:"variant,omitempty"`
	RegistrationNumber *string   `json:"registration_number,omitempty"`
	BatteryCapacityWh  *int64    `json:"battery_capacity_wh,omitempty"`
	SupportsAC         *bool     `json:"supports_ac,omitempty"`
	SupportsDC         *bool     `json:"supports_dc,omitempty"`
	MaxACPowerW        *int32    `json:"max_ac_power_w,omitempty"`
	MaxDCPowerW        *int32    `json:"max_dc_power_w,omitempty"`
	ConnectorTypes     []string  `json:"connector_types"`
	IsActive           bool      `json:"is_active"`
	CreatedAt          time.Time `json:"created_at"`
	UpdatedAt          time.Time `json:"updated_at"`
	Version            int64     `json:"version"`
}

// CreateVehicleRequest captures vehicle registration payload.
type CreateVehicleRequest struct {
	Label              *string  `json:"label"`
	Make               string   `json:"make"`
	Model              string   `json:"model"`
	Variant            *string  `json:"variant"`
	RegistrationNumber *string  `json:"registration_number"`
	BatteryCapacityWh  *int64   `json:"battery_capacity_wh"`
	SupportsAC         *bool    `json:"supports_ac"`
	SupportsDC         *bool    `json:"supports_dc"`
	MaxACPowerW        *int32   `json:"max_ac_power_w"`
	MaxDCPowerW        *int32   `json:"max_dc_power_w"`
	ConnectorTypes     []string `json:"connector_types"`
}

// Validate validates CreateVehicleRequest against schema invariants.
func (r *CreateVehicleRequest) Validate() error {
	trimmedMake := strings.TrimSpace(r.Make)
	if len(trimmedMake) < 1 || len(trimmedMake) > 120 {
		return fmt.Errorf("%w: make is required and must be between 1 and 120 characters", ErrInvalid)
	}
	trimmedModel := strings.TrimSpace(r.Model)
	if len(trimmedModel) < 1 || len(trimmedModel) > 120 {
		return fmt.Errorf("%w: model is required and must be between 1 and 120 characters", ErrInvalid)
	}
	if r.Label != nil {
		trimmed := strings.TrimSpace(*r.Label)
		if len(trimmed) < 1 || len(trimmed) > 100 {
			return fmt.Errorf("%w: label must be between 1 and 100 characters", ErrInvalid)
		}
	}
	if r.RegistrationNumber != nil {
		trimmed := strings.ToUpper(strings.TrimSpace(*r.RegistrationNumber))
		if len(trimmed) < 2 || len(trimmed) > 32 || !regNumberRe.MatchString(trimmed) {
			return fmt.Errorf("%w: registration_number must be 2-32 uppercase alphanumeric characters", ErrInvalid)
		}
	}
	if r.BatteryCapacityWh != nil && *r.BatteryCapacityWh <= 0 {
		return fmt.Errorf("%w: battery_capacity_wh must be positive", ErrInvalid)
	}
	if r.SupportsAC != nil && !*r.SupportsAC && r.MaxACPowerW != nil && *r.MaxACPowerW > 0 {
		return fmt.Errorf("%w: max_ac_power_w cannot be specified when supports_ac is false", ErrInvalid)
	}
	if r.SupportsDC != nil && !*r.SupportsDC && r.MaxDCPowerW != nil && *r.MaxDCPowerW > 0 {
		return fmt.Errorf("%w: max_dc_power_w cannot be specified when supports_dc is false", ErrInvalid)
	}
	if r.MaxACPowerW != nil && *r.MaxACPowerW <= 0 {
		return fmt.Errorf("%w: max_ac_power_w must be positive", ErrInvalid)
	}
	if r.MaxDCPowerW != nil && *r.MaxDCPowerW <= 0 {
		return fmt.Errorf("%w: max_dc_power_w must be positive", ErrInvalid)
	}
	return nil
}

// UpdateVehicleRequest captures updates to an existing vehicle for PATCH/PUT.
type UpdateVehicleRequest struct {
	Label              *string  `json:"label,omitempty"`
	Make               *string  `json:"make,omitempty"`
	Model              *string  `json:"model,omitempty"`
	Variant            *string  `json:"variant,omitempty"`
	RegistrationNumber *string  `json:"registration_number,omitempty"`
	BatteryCapacityWh  *int64   `json:"battery_capacity_wh,omitempty"`
	SupportsAC         *bool    `json:"supports_ac,omitempty"`
	SupportsDC         *bool    `json:"supports_dc,omitempty"`
	MaxACPowerW        *int32   `json:"max_ac_power_w,omitempty"`
	MaxDCPowerW        *int32   `json:"max_dc_power_w,omitempty"`
	ConnectorTypes     []string `json:"connector_types,omitempty"`
	IsActive           *bool    `json:"is_active,omitempty"`
	ExpectedVersion    int64    `json:"expected_version,omitempty"`
}

// Validate validates UpdateVehicleRequest.
func (r *UpdateVehicleRequest) Validate() error {
	if r.ExpectedVersion < 0 {
		return fmt.Errorf("%w: expected_version cannot be negative", ErrInvalid)
	}
	if r.Make != nil {
		trimmed := strings.TrimSpace(*r.Make)
		if len(trimmed) < 1 || len(trimmed) > 120 {
			return fmt.Errorf("%w: make must be between 1 and 120 characters", ErrInvalid)
		}
	}
	if r.Model != nil {
		trimmed := strings.TrimSpace(*r.Model)
		if len(trimmed) < 1 || len(trimmed) > 120 {
			return fmt.Errorf("%w: model must be between 1 and 120 characters", ErrInvalid)
		}
	}
	if r.Label != nil {
		trimmed := strings.TrimSpace(*r.Label)
		if len(trimmed) < 1 || len(trimmed) > 100 {
			return fmt.Errorf("%w: label must be between 1 and 100 characters", ErrInvalid)
		}
	}
	if r.RegistrationNumber != nil {
		trimmed := strings.ToUpper(strings.TrimSpace(*r.RegistrationNumber))
		if len(trimmed) < 2 || len(trimmed) > 32 || !regNumberRe.MatchString(trimmed) {
			return fmt.Errorf("%w: registration_number must be 2-32 uppercase alphanumeric characters", ErrInvalid)
		}
	}
	if r.BatteryCapacityWh != nil && *r.BatteryCapacityWh <= 0 {
		return fmt.Errorf("%w: battery_capacity_wh must be positive", ErrInvalid)
	}
	if r.SupportsAC != nil && !*r.SupportsAC && r.MaxACPowerW != nil && *r.MaxACPowerW > 0 {
		return fmt.Errorf("%w: max_ac_power_w cannot be specified when supports_ac is false", ErrInvalid)
	}
	if r.SupportsDC != nil && !*r.SupportsDC && r.MaxDCPowerW != nil && *r.MaxDCPowerW > 0 {
		return fmt.Errorf("%w: max_dc_power_w cannot be specified when supports_dc is false", ErrInvalid)
	}
	return nil
}

// CustomerAddress represents a saved service location.
type CustomerAddress struct {
	ID                   uuid.UUID `json:"id"`
	CustomerID           uuid.UUID `json:"customer_id"`
	Label                *string   `json:"label,omitempty"`
	Line1                string    `json:"line1"`
	Line2                *string   `json:"line2,omitempty"`
	Locality             *string   `json:"locality,omitempty"`
	City                 string    `json:"city"`
	State                string    `json:"state"`
	PostalCode           *string   `json:"postal_code,omitempty"`
	CountryCode          string    `json:"country_code"`
	Latitude             *string   `json:"latitude,omitempty"`
	Longitude            *string   `json:"longitude,omitempty"`
	Landmark             *string   `json:"landmark,omitempty"`
	BuildingInstructions *string   `json:"building_instructions,omitempty"`
	Floor                *string   `json:"floor,omitempty"`
	ContactInstructions  *string   `json:"contact_instructions,omitempty"`
	GeocodeProvider      *string   `json:"geocode_provider,omitempty"`
	GeocodeExternalRef   *string   `json:"geocode_external_ref,omitempty"`
	IsDefault            bool      `json:"is_default"`
	IsActive             bool      `json:"is_active"`
	CreatedAt            time.Time `json:"created_at"`
	UpdatedAt            time.Time `json:"updated_at"`
	Version              int64     `json:"version"`
}

// CreateAddressRequest captures request payload to save a new address.
type CreateAddressRequest struct {
	Label                *string `json:"label"`
	Line1                string  `json:"line1"`
	Line2                *string `json:"line2"`
	Locality             *string `json:"locality"`
	City                 string  `json:"city"`
	State                string  `json:"state"`
	PostalCode           *string `json:"postal_code"`
	CountryCode          *string `json:"country_code"`
	Latitude             *string `json:"latitude"`
	Longitude            *string `json:"longitude"`
	Landmark             *string `json:"landmark"`
	BuildingInstructions *string `json:"building_instructions"`
	Floor                *string `json:"floor"`
	ContactInstructions  *string `json:"contact_instructions"`
	GeocodeProvider      *string `json:"geocode_provider"`
	GeocodeExternalRef   *string `json:"geocode_external_ref"`
	IsDefault            bool    `json:"is_default"`
}

// Validate validates CreateAddressRequest according to database CHECK constraints.
func (r *CreateAddressRequest) Validate() error {
	trimmedLine1 := strings.TrimSpace(r.Line1)
	if len(trimmedLine1) < 1 || len(trimmedLine1) > 240 {
		return fmt.Errorf("%w: line1 is required and must be between 1 and 240 characters", ErrInvalid)
	}
	trimmedCity := strings.TrimSpace(r.City)
	if len(trimmedCity) < 1 || len(trimmedCity) > 120 {
		return fmt.Errorf("%w: city is required and must be between 1 and 120 characters", ErrInvalid)
	}
	trimmedState := strings.TrimSpace(r.State)
	if len(trimmedState) < 1 || len(trimmedState) > 120 {
		return fmt.Errorf("%w: state is required and must be between 1 and 120 characters", ErrInvalid)
	}
	if r.Label != nil {
		trimmed := strings.TrimSpace(*r.Label)
		if len(trimmed) < 1 || len(trimmed) > 80 {
			return fmt.Errorf("%w: label must be between 1 and 80 characters", ErrInvalid)
		}
	}
	if r.PostalCode != nil {
		trimmed := strings.TrimSpace(*r.PostalCode)
		if len(trimmed) < 3 || len(trimmed) > 16 {
			return fmt.Errorf("%w: postal_code must be between 3 and 16 characters", ErrInvalid)
		}
	}
	if r.CountryCode != nil {
		code := strings.ToUpper(strings.TrimSpace(*r.CountryCode))
		if !countryCodeRe.MatchString(code) {
			return fmt.Errorf("%w: country_code must be exactly 2 uppercase letters", ErrInvalid)
		}
	}
	// Latitude and longitude must either both be nil or both be non-nil.
	if (r.Latitude == nil) != (r.Longitude == nil) {
		return fmt.Errorf("%w: latitude and longitude must both be provided together", ErrInvalid)
	}
	if r.Latitude != nil && r.Longitude != nil {
		lat, err := decimal.NewFromString(*r.Latitude)
		if err != nil {
			return fmt.Errorf("%w: invalid latitude format", ErrInvalid)
		}
		if lat.LessThan(decimal.NewFromInt(-90)) || lat.GreaterThan(decimal.NewFromInt(90)) {
			return fmt.Errorf("%w: latitude must be between -90 and 90", ErrInvalid)
		}
		lon, err := decimal.NewFromString(*r.Longitude)
		if err != nil {
			return fmt.Errorf("%w: invalid longitude format", ErrInvalid)
		}
		if lon.LessThan(decimal.NewFromInt(-180)) || lon.GreaterThan(decimal.NewFromInt(180)) {
			return fmt.Errorf("%w: longitude must be between -180 and 180", ErrInvalid)
		}
	}
	return nil
}

// UpdateAddressRequest captures address modification payload for PATCH/PUT.
type UpdateAddressRequest struct {
	Label                *string `json:"label,omitempty"`
	Line1                *string `json:"line1,omitempty"`
	Line2                *string `json:"line2,omitempty"`
	Locality             *string `json:"locality,omitempty"`
	City                 *string `json:"city,omitempty"`
	State                *string `json:"state,omitempty"`
	PostalCode           *string `json:"postal_code,omitempty"`
	CountryCode          *string `json:"country_code,omitempty"`
	Latitude             *string `json:"latitude,omitempty"`
	Longitude            *string `json:"longitude,omitempty"`
	Landmark             *string `json:"landmark,omitempty"`
	BuildingInstructions *string `json:"building_instructions,omitempty"`
	Floor                *string `json:"floor,omitempty"`
	ContactInstructions  *string `json:"contact_instructions,omitempty"`
	GeocodeProvider      *string `json:"geocode_provider,omitempty"`
	GeocodeExternalRef   *string `json:"geocode_external_ref,omitempty"`
	IsDefault            *bool   `json:"is_default,omitempty"`
	IsActive             *bool   `json:"is_active,omitempty"`
	ExpectedVersion      int64   `json:"expected_version,omitempty"`
}

// Validate validates UpdateAddressRequest.
func (r *UpdateAddressRequest) Validate() error {
	if r.ExpectedVersion < 0 {
		return fmt.Errorf("%w: expected_version cannot be negative", ErrInvalid)
	}
	if r.Line1 != nil {
		trimmed := strings.TrimSpace(*r.Line1)
		if len(trimmed) < 1 || len(trimmed) > 240 {
			return fmt.Errorf("%w: line1 must be between 1 and 240 characters", ErrInvalid)
		}
	}
	if r.City != nil {
		trimmed := strings.TrimSpace(*r.City)
		if len(trimmed) < 1 || len(trimmed) > 120 {
			return fmt.Errorf("%w: city must be between 1 and 120 characters", ErrInvalid)
		}
	}
	if r.State != nil {
		trimmed := strings.TrimSpace(*r.State)
		if len(trimmed) < 1 || len(trimmed) > 120 {
			return fmt.Errorf("%w: state must be between 1 and 120 characters", ErrInvalid)
		}
	}
	if r.Label != nil {
		trimmed := strings.TrimSpace(*r.Label)
		if len(trimmed) < 1 || len(trimmed) > 80 {
			return fmt.Errorf("%w: label must be between 1 and 80 characters", ErrInvalid)
		}
	}
	if r.PostalCode != nil {
		trimmed := strings.TrimSpace(*r.PostalCode)
		if len(trimmed) < 3 || len(trimmed) > 16 {
			return fmt.Errorf("%w: postal_code must be between 3 and 16 characters", ErrInvalid)
		}
	}
	if r.CountryCode != nil {
		code := strings.ToUpper(strings.TrimSpace(*r.CountryCode))
		if !countryCodeRe.MatchString(code) {
			return fmt.Errorf("%w: country_code must be exactly 2 uppercase letters", ErrInvalid)
		}
	}
	if r.Latitude != nil && r.Longitude != nil {
		lat, err := decimal.NewFromString(*r.Latitude)
		if err != nil {
			return fmt.Errorf("%w: invalid latitude format", ErrInvalid)
		}
		if lat.LessThan(decimal.NewFromInt(-90)) || lat.GreaterThan(decimal.NewFromInt(90)) {
			return fmt.Errorf("%w: latitude must be between -90 and 90", ErrInvalid)
		}
		lon, err := decimal.NewFromString(*r.Longitude)
		if err != nil {
			return fmt.Errorf("%w: invalid longitude format", ErrInvalid)
		}
		if lon.LessThan(decimal.NewFromInt(-180)) || lon.GreaterThan(decimal.NewFromInt(180)) {
			return fmt.Errorf("%w: longitude must be between -180 and 180", ErrInvalid)
		}
	}
	if r.IsDefault != nil && *r.IsDefault && r.IsActive != nil && !*r.IsActive {
		return fmt.Errorf("%w: address cannot be both default and inactive", ErrInvalid)
	}
	return nil
}
