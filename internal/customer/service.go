// Package customer provides business logic for customer profile, vehicle, and address domains.
package customer

import (
	"context"

	"github.com/google/uuid"
)

// Service coordinates customer-plane operations with domain validation and tenant isolation.
type Service struct {
	Repo Repository
}

// NewService constructs a customer Service with the given repository.
func NewService(repo Repository) *Service {
	return &Service{Repo: repo}
}

// GetProfile retrieves or bootstraps the customer profile for an authenticated app account.
func (s *Service) GetProfile(ctx context.Context, accountID uuid.UUID) (*CustomerProfile, error) {
	return s.Repo.EnsureProfile(ctx, accountID)
}

// CreateProfile creates a new profile for an app account.
func (s *Service) CreateProfile(ctx context.Context, accountID uuid.UUID, req *CreateProfileRequest) (*CustomerProfile, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	return s.Repo.CreateProfile(ctx, accountID, req, &accountID)
}

// UpdateProfile validates and applies updates to the authenticated account's customer profile.
func (s *Service) UpdateProfile(ctx context.Context, accountID uuid.UUID, req *UpdateProfileRequest) (*CustomerProfile, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.UpdateProfile(ctx, profile.ID, req, &accountID)
}

// CreateVehicle registers a new vehicle for the customer.
func (s *Service) CreateVehicle(ctx context.Context, accountID uuid.UUID, req *CreateVehicleRequest) (*CustomerVehicle, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.CreateVehicle(ctx, profile.ID, req, &accountID)
}

// GetVehicle retrieves a specific vehicle owned by the authenticated customer.
func (s *Service) GetVehicle(ctx context.Context, accountID, vehicleID uuid.UUID) (*CustomerVehicle, error) {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.GetVehicleByID(ctx, profile.ID, vehicleID)
}

// ListVehicles returns all registered vehicles for the authenticated customer.
func (s *Service) ListVehicles(ctx context.Context, accountID uuid.UUID, activeOnly bool) ([]CustomerVehicle, error) {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.ListVehicles(ctx, profile.ID, activeOnly)
}

// UpdateVehicle updates an existing vehicle owned by the customer with optimistic concurrency.
func (s *Service) UpdateVehicle(ctx context.Context, accountID, vehicleID uuid.UUID, req *UpdateVehicleRequest) (*CustomerVehicle, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.UpdateVehicle(ctx, profile.ID, vehicleID, req, &accountID)
}

// DeleteVehicle deactivates a customer vehicle.
func (s *Service) DeleteVehicle(ctx context.Context, accountID, vehicleID uuid.UUID) error {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return err
	}
	return s.Repo.DeleteVehicle(ctx, profile.ID, vehicleID, &accountID)
}

// CreateAddress creates a new saved address for the customer.
func (s *Service) CreateAddress(ctx context.Context, accountID uuid.UUID, req *CreateAddressRequest) (*CustomerAddress, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.CreateAddress(ctx, profile.ID, req, &accountID)
}

// GetAddress retrieves a saved address by ID ensuring it belongs to the authenticated customer.
func (s *Service) GetAddress(ctx context.Context, accountID, addressID uuid.UUID) (*CustomerAddress, error) {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.GetAddressByID(ctx, profile.ID, addressID)
}

// ListAddresses lists saved addresses for the customer.
func (s *Service) ListAddresses(ctx context.Context, accountID uuid.UUID, activeOnly bool) ([]CustomerAddress, error) {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.ListAddresses(ctx, profile.ID, activeOnly)
}

// UpdateAddress updates an existing address owned by the customer.
func (s *Service) UpdateAddress(ctx context.Context, accountID, addressID uuid.UUID, req *UpdateAddressRequest) (*CustomerAddress, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return nil, err
	}
	return s.Repo.UpdateAddress(ctx, profile.ID, addressID, req, &accountID)
}

// DeleteAddress deactivates a customer address.
func (s *Service) DeleteAddress(ctx context.Context, accountID, addressID uuid.UUID) error {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return err
	}
	return s.Repo.DeleteAddress(ctx, profile.ID, addressID, &accountID)
}

// SetDefaultAddress switches an address to be the sole active default address.
func (s *Service) SetDefaultAddress(ctx context.Context, accountID, addressID uuid.UUID) error {
	profile, err := s.Repo.EnsureProfile(ctx, accountID)
	if err != nil {
		return err
	}
	return s.Repo.SetDefaultAddress(ctx, profile.ID, addressID, &accountID)
}
