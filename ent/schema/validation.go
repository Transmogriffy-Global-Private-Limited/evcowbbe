package schema

import "fmt"

func only(value, expected string) error {
	if value != expected {
		return fmt.Errorf("expected %q", expected)
	}
	return nil
}
