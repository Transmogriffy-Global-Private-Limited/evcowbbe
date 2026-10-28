// Package pricing evaluates frozen commercial terms for a mobile charging job.
// It does not infer measurements, geography, tax law, or HAL state.
package pricing

import (
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"sort"
	"strings"
)

var ErrInvalidPolicy = errors.New("invalid pricing policy")
var ErrMissingEvidence = errors.New("required pricing evidence unavailable")
var ErrTaxUnavailable = errors.New("approved tax treatment unavailable")
var ErrAmountOverflow = errors.New("pricing amount exceeds supported monetary range")

const (
	BaseRule       = "BASE"
	OverrideRule   = "OVERRIDE_RATE"
	MultiplierRule = "MULTIPLIER"
	DiscountRule   = "DISCOUNT_PERCENT"
	Preview        = "PREVIEW"
	Final          = "FINAL"
)

var decimals = regexp.MustCompile(`^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$`)
var identifiers = regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,63}$`)

// Rule's price is a decimal currency amount per unit for BASE/OVERRIDE_RATE,
// a dimensionless factor for MULTIPLIER, or a percent for DISCOUNT_PERCENT.
// A modifier's Target is the ID of exactly one BASE rule.
type Rule struct {
	ID         string            `json:"id"`
	Kind       string            `json:"kind"`
	Component  string            `json:"component,omitempty"`
	Basis      string            `json:"basis,omitempty"`
	Rate       string            `json:"rate"`
	Target     string            `json:"target,omitempty"`
	TaxClass   string            `json:"tax_class,omitempty"`
	Priority   int               `json:"priority"`
	Conditions map[string]string `json:"conditions,omitempty"`
}

// Terms are already resolved and frozen by the policy and quote owners. They
// must never be constructed from untrusted client-supplied tariff IDs alone.
type Terms struct {
	PolicyID      string `json:"policy_id"`
	PolicyVersion int64  `json:"policy_version"`
	Currency      string `json:"currency"`
	Rules         []Rule `json:"rules"`
}

// Measurement is a physical/service fact, or a clearly marked quote estimate.
// FINAL requires a durable reference for each consumed measurement.
type Measurement struct {
	Quantity string `json:"quantity"`
	Source   string `json:"source"`
	Estimate bool   `json:"estimate"`
}

type Evidence struct {
	Measurements map[string]Measurement `json:"measurements"`
	Conditions   map[string]string      `json:"conditions"`
}

// TaxClass contains independently approved rates. Empty tax configuration is
// not zero tax; an explicitly approved zero-rate class can represent zero.
type TaxClass struct {
	Approved bool   `json:"approved"`
	SGST     string `json:"sgst_percent"`
	CGST     string `json:"cgst_percent"`
	IGST     string `json:"igst_percent"`
}

type Line struct {
	RuleID       string `json:"rule_id"`
	Component    string `json:"component"`
	Kind         string `json:"kind"`
	Basis        string `json:"basis,omitempty"`
	Quantity     string `json:"quantity,omitempty"`
	Rate         string `json:"rate"`
	AmountMinor  int64  `json:"amount_minor"`
	TaxClass     string `json:"tax_class,omitempty"`
	Source       string `json:"source,omitempty"`
	Estimate     bool   `json:"estimate,omitempty"`
	TargetRuleID string `json:"target_rule_id,omitempty"`
}

type Result struct {
	PolicyID      string `json:"policy_id"`
	PolicyVersion int64  `json:"policy_version"`
	Currency      string `json:"currency"`
	Mode          string `json:"mode"`
	Estimated     bool   `json:"estimated"`
	Lines         []Line `json:"lines"`
	SubtotalMinor int64  `json:"subtotal_minor"`
	TaxMinor      int64  `json:"tax_minor"`
	TotalMinor    int64  `json:"total_minor"`
}

func decimal(raw string) (*big.Rat, error) {
	if !decimals.MatchString(raw) {
		return nil, ErrInvalidPolicy
	}
	value, ok := new(big.Rat).SetString(raw)
	if !ok {
		return nil, ErrInvalidPolicy
	}
	return value, nil
}

func positiveDecimal(raw string) (*big.Rat, error) {
	v, err := decimal(raw)
	if err != nil || v.Sign() <= 0 {
		return nil, ErrInvalidPolicy
	}
	return v, nil
}

func roundNonnegative(value *big.Rat) (int64, error) {
	if value.Sign() < 0 {
		return 0, ErrInvalidPolicy
	}
	quotient, remainder := new(big.Int).QuoRem(value.Num(), value.Denom(), new(big.Int))
	if new(big.Int).Mul(remainder, big.NewInt(2)).Cmp(value.Denom()) >= 0 {
		quotient.Add(quotient, big.NewInt(1))
	}
	if !quotient.IsInt64() {
		return 0, ErrAmountOverflow
	}
	return quotient.Int64(), nil
}

func roundedMinor(rupees *big.Rat) (int64, error) {
	return roundNonnegative(new(big.Rat).Mul(rupees, big.NewRat(100, 1)))
}

func checkedAdd(a, b int64) (int64, error) {
	sum := new(big.Int).Add(big.NewInt(a), big.NewInt(b))
	if !sum.IsInt64() {
		return 0, ErrAmountOverflow
	}
	return sum.Int64(), nil
}

func validID(s string) bool { return identifiers.MatchString(s) }

// Validate is independent of pricing amounts and prevents ambiguous rule
// composition before a policy is ever eligible for publication.
func Validate(terms Terms) error {
	if terms.PolicyID == "" || terms.PolicyVersion < 1 || terms.Currency != "INR" || len(terms.Rules) == 0 || len(terms.Rules) > 128 {
		return ErrInvalidPolicy
	}
	base := map[string]Rule{}
	components := map[string]bool{}
	ids := map[string]bool{}
	overrides := map[string]bool{}
	for _, rule := range terms.Rules {
		if !validID(rule.ID) || ids[rule.ID] || rule.Priority < 0 || rule.Priority > 100000 {
			return ErrInvalidPolicy
		}
		ids[rule.ID] = true
		if _, err := decimal(rule.Rate); err != nil {
			return err
		}
		for key, value := range rule.Conditions {
			if !validID(key) || !validID(value) {
				return ErrInvalidPolicy
			}
		}
		switch rule.Kind {
		case BaseRule:
			if !validID(rule.Component) || !validID(rule.Basis) || !validID(rule.TaxClass) || rule.Target != "" {
				return ErrInvalidPolicy
			}
			// Independent monetary components may be added, but two BASE
			// definitions of the same component would silently double-charge
			// when both conditions match. Use a conditional override instead.
			if components[rule.Component] {
				return ErrInvalidPolicy
			}
			components[rule.Component] = true
			base[rule.ID] = rule
		case OverrideRule, MultiplierRule, DiscountRule:
			if !validID(rule.Target) || rule.Component != "" || rule.Basis != "" || rule.TaxClass != "" {
				return ErrInvalidPolicy
			}
			if rule.Kind == OverrideRule {
				if overrides[rule.Target] {
					return ErrInvalidPolicy
				}
				overrides[rule.Target] = true
			}
			if rule.Kind == DiscountRule {
				percent, _ := decimal(rule.Rate)
				if percent.Cmp(big.NewRat(100, 1)) > 0 {
					return ErrInvalidPolicy
				}
			}
		default:
			return ErrInvalidPolicy
		}
	}
	if len(base) == 0 {
		return ErrInvalidPolicy
	}
	for _, rule := range terms.Rules {
		if rule.Kind != BaseRule {
			if _, ok := base[rule.Target]; !ok {
				return ErrInvalidPolicy
			}
		}
	}
	return nil
}

func conditionsMatch(required, actual map[string]string) bool {
	for key, value := range required {
		if actual[key] != value {
			return false
		}
	}
	return true
}

// Evaluate always sums the same integer-minor line items it exposes. Prices
// cannot silently fall back, and missing tax profiles never imply zero tax.
// The caller owns policy selection, quote expiry and authoritative HAL/job facts.
func Evaluate(terms Terms, facts Evidence, tax map[string]TaxClass, mode string) (Result, error) {
	if err := Validate(terms); err != nil {
		return Result{}, err
	}
	if mode != Preview && mode != Final {
		return Result{}, ErrInvalidPolicy
	}
	// A missing condition is unknown, not false: silently omitting a peak or
	// priority surcharge could undercharge a job. The trusted caller must
	// explicitly supply every condition dimension referenced by these terms.
	for _, rule := range terms.Rules {
		for key := range rule.Conditions {
			value, ok := facts.Conditions[key]
			if !ok || !validID(value) {
				return Result{}, fmt.Errorf("%w: condition %s", ErrMissingEvidence, key)
			}
		}
	}
	result := Result{PolicyID: terms.PolicyID, PolicyVersion: terms.PolicyVersion, Currency: terms.Currency, Mode: mode}
	baseRules := make([]Rule, 0, len(terms.Rules))
	modifiers := map[string][]Rule{}
	for _, rule := range terms.Rules {
		if rule.Kind == BaseRule {
			baseRules = append(baseRules, rule)
		} else {
			modifiers[rule.Target] = append(modifiers[rule.Target], rule)
		}
	}
	sort.Slice(baseRules, func(i, j int) bool {
		if baseRules[i].Priority == baseRules[j].Priority {
			return baseRules[i].ID < baseRules[j].ID
		}
		return baseRules[i].Priority < baseRules[j].Priority
	})
	taxable := map[string]int64{}
	for _, rule := range baseRules {
		if !conditionsMatch(rule.Conditions, facts.Conditions) {
			continue
		}
		measurement, ok := facts.Measurements[rule.Basis]
		if !ok || strings.TrimSpace(measurement.Source) == "" {
			return Result{}, fmt.Errorf("%w: %s", ErrMissingEvidence, rule.Basis)
		}
		if mode == Final && measurement.Estimate {
			return Result{}, fmt.Errorf("%w: estimated %s", ErrMissingEvidence, rule.Basis)
		}
		quantity, err := decimal(measurement.Quantity)
		if err != nil {
			return Result{}, err
		}
		rate, _ := decimal(rule.Rate)
		applicable := make([]Rule, 0, len(modifiers[rule.ID]))
		for _, modifier := range modifiers[rule.ID] {
			if conditionsMatch(modifier.Conditions, facts.Conditions) {
				applicable = append(applicable, modifier)
			}
		}
		for _, modifier := range applicable {
			if modifier.Kind == OverrideRule {
				rate, _ = decimal(modifier.Rate)
			}
		}
		amount, err := roundedMinor(new(big.Rat).Mul(rate, quantity))
		if err != nil {
			return Result{}, err
		}
		result.Lines = append(result.Lines, Line{RuleID: rule.ID, Component: rule.Component, Kind: rule.Kind, Basis: rule.Basis, Quantity: measurement.Quantity, Rate: rate.FloatString(6), AmountMinor: amount, TaxClass: rule.TaxClass, Source: measurement.Source, Estimate: measurement.Estimate})
		result.Estimated = result.Estimated || measurement.Estimate
		current := amount
		sort.Slice(applicable, func(i, j int) bool {
			if applicable[i].Priority == applicable[j].Priority {
				return applicable[i].ID < applicable[j].ID
			}
			return applicable[i].Priority < applicable[j].Priority
		})
		for _, modifier := range applicable {
			if modifier.Kind == OverrideRule {
				continue
			}
			factor, _ := decimal(modifier.Rate)
			var change int64
			switch modifier.Kind {
			case MultiplierRule:
				percentage := new(big.Rat).Mul(new(big.Rat).SetInt64(current), factor)
				percentage.Quo(percentage, big.NewRat(100, 1))
				newAmount, roundErr := roundNonnegative(percentage)
				if roundErr != nil {
					return Result{}, roundErr
				}
				// Multiplier factors are represented as percentage points; 100 means unchanged.
				change = newAmount - current
			case DiscountRule:
				percentage := new(big.Rat).Mul(new(big.Rat).SetInt64(current), factor)
				percentage.Quo(percentage, big.NewRat(100, 1))
				change, err = roundNonnegative(percentage)
				if err != nil {
					return Result{}, err
				}
				change = -change
			}
			current, err = checkedAdd(current, change)
			if err != nil || current < 0 {
				return Result{}, ErrInvalidPolicy
			}
			result.Lines = append(result.Lines, Line{RuleID: modifier.ID, Component: rule.Component, Kind: modifier.Kind, Rate: modifier.Rate, AmountMinor: change, TaxClass: rule.TaxClass, TargetRuleID: rule.ID})
		}
		result.SubtotalMinor, err = checkedAdd(result.SubtotalMinor, current)
		if err != nil {
			return Result{}, err
		}
		taxable[rule.TaxClass], err = checkedAdd(taxable[rule.TaxClass], current)
		if err != nil {
			return Result{}, err
		}
	}
	classes := make([]string, 0, len(taxable))
	if len(result.Lines) == 0 {
		return Result{}, ErrNoApplicablePolicy
	}
	for class := range taxable {
		classes = append(classes, class)
	}
	sort.Strings(classes)
	for _, class := range classes {
		profile, ok := tax[class]
		if !ok || !profile.Approved {
			return Result{}, fmt.Errorf("%w: %s", ErrTaxUnavailable, class)
		}
		values := []struct{ name, rate string }{{"SGST", profile.SGST}, {"CGST", profile.CGST}, {"IGST", profile.IGST}}
		parsed := make([]*big.Rat, 0, 3)
		for _, item := range values {
			p, err := decimal(item.rate)
			if err != nil || p.Cmp(big.NewRat(100, 1)) > 0 {
				return Result{}, ErrTaxUnavailable
			}
			parsed = append(parsed, p)
		}
		if parsed[2].Sign() != 0 && (parsed[0].Sign() != 0 || parsed[1].Sign() != 0) {
			return Result{}, ErrTaxUnavailable
		}
		for i, item := range values {
			if parsed[i].Sign() == 0 {
				continue
			}
			// Tax rates are percentages of integer-paise taxable amounts.
			value := new(big.Rat).Mul(new(big.Rat).SetInt64(taxable[class]), parsed[i])
			value.Quo(value, big.NewRat(100, 1))
			minor, err := roundNonnegative(value)
			if err != nil {
				return Result{}, err
			}
			result.TaxMinor, err = checkedAdd(result.TaxMinor, minor)
			if err != nil {
				return Result{}, err
			}
			result.Lines = append(result.Lines, Line{RuleID: "TAX_" + class + "_" + item.name, Component: item.name, Kind: "TAX", Rate: item.rate, AmountMinor: minor, TaxClass: class})
		}
	}
	var err error
	result.TotalMinor, err = checkedAdd(result.SubtotalMinor, result.TaxMinor)
	return result, err
}
