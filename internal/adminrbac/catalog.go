package adminrbac

import "sort"

// Exact capability namespace and role defaults from blueprint Annex 44.
// No wildcard, implicit hierarchy or role-name authorization bypass exists.
var capabilities = []string{
	"users.read", "users.manage", "drivers.read", "drivers.review", "drivers.manage", "drivers.suspend",
	"fleet.read", "fleet.manage", "fleet.maintenance", "fleet.assign", "schedules.read", "schedules.manage",
	"dispatch.read", "dispatch.manage", "dispatch.override", "jobs.read", "jobs.manage", "jobs.cancel", "jobs.reassign",
	"charging.read", "charging.command", "charging.reconcile", "charging.trace.read",
	"pricing.read", "pricing.manage", "payments.read", "payments.manage", "refunds.manage",
	"invoices.read", "invoices.manage", "compensation.read", "compensation.manage", "compensation.adjust",
	"ratings.read", "support.read", "support.reply", "support.manage", "incidents.read", "incidents.manage",
	"notifications.read", "notifications.manage", "exports.manage", "system.settings.read", "system.settings.manage", "audit.read",
}

var roles = map[string][]string{
	"OPERATIONS": {
		"users.read", "drivers.read", "drivers.manage", "fleet.read", "fleet.manage", "fleet.maintenance", "fleet.assign",
		"schedules.read", "schedules.manage", "dispatch.read", "dispatch.manage", "dispatch.override",
		"jobs.read", "jobs.manage", "jobs.cancel", "jobs.reassign", "charging.read", "charging.command", "charging.reconcile",
		"ratings.read", "support.read", "support.reply", "support.manage", "incidents.read", "incidents.manage",
		"notifications.read", "exports.manage", "system.settings.read", "audit.read",
	},
	"DRIVER_RECRUITMENT": {"drivers.read", "drivers.review", "drivers.manage", "drivers.suspend", "schedules.read", "fleet.read", "incidents.read", "support.read", "audit.read"},
	"FINANCE_COMMERCIAL": {"users.read", "jobs.read", "pricing.read", "pricing.manage", "payments.read", "payments.manage", "refunds.manage", "invoices.read", "invoices.manage", "compensation.read", "compensation.manage", "compensation.adjust", "ratings.read", "support.read", "incidents.read", "exports.manage", "audit.read"},
	"SUPPORT":            {"users.read", "drivers.read", "jobs.read", "charging.read", "payments.read", "invoices.read", "ratings.read", "support.read", "support.reply", "support.manage", "incidents.read", "incidents.manage", "notifications.read"},
	"TECHNICAL_HAL":      {"fleet.read", "charging.read", "charging.command", "charging.reconcile", "charging.trace.read", "jobs.read", "incidents.read", "incidents.manage", "system.settings.read", "audit.read"},
	"READ_ONLY":          {"users.read", "drivers.read", "fleet.read", "schedules.read", "dispatch.read", "jobs.read", "charging.read", "pricing.read", "payments.read", "invoices.read", "compensation.read", "ratings.read", "support.read", "incidents.read", "notifications.read", "system.settings.read"},
}

func init() { roles["FULL_ADMIN"] = append([]string(nil), capabilities...) }

func ValidCapability(c string) bool {
	for _, v := range capabilities {
		if v == c {
			return true
		}
	}
	return false
}
func ValidRole(r string) bool { _, ok := roles[r]; return ok }
func Capabilities() []string  { return append([]string(nil), capabilities...) }
func Catalog() map[string][]string {
	m := make(map[string][]string, len(roles))
	for r, c := range roles {
		m[r] = append([]string(nil), c...)
		sort.Strings(m[r])
	}
	return m
}

func Evaluate(assigned []string, overrides map[string]string) []string {
	granted := map[string]bool{}
	for _, r := range assigned {
		for _, cap := range roles[r] {
			granted[cap] = true
		}
	}
	for cap, decision := range overrides {
		if !ValidCapability(cap) {
			continue
		}
		if decision == "DENY" {
			delete(granted, cap)
		} else if decision == "ALLOW" {
			granted[cap] = true
		}
	}
	out := make([]string, 0, len(granted))
	for cap := range granted {
		out = append(out, cap)
	}
	sort.Strings(out)
	return out
}
