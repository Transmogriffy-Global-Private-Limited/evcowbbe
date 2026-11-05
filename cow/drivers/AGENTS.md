# Driver feature boundary

Read `cow/drivers/README.md` and `docs/ROLE_MODULE_ARCHITECTURE_20261105.md` first.
Own driver-specific HTTP routes and role-specific orchestration here. Shared
identity, schema, pricing, dispatch admission, wallet, HAL and migrations belong
to core. Never accept self-asserted eligibility/availability or physical charging
requests as authoritative. Preserve existing driver auth/OTP contracts.
