package buildinfo

import "testing"

func TestIsValidGitSHA(t *testing.T) {
	valid := "0123456789abcdef0123456789abcdef01234567"

	if !IsValidGitSHA(valid) {
		t.Fatal("expected a 40-character lowercase Git SHA to be valid")
	}

	for _, value := range []string{
		"",
		"0123456789abcdef",
		"0123456789ABCDEF0123456789abcdef01234567",
		"g123456789abcdef0123456789abcdef01234567",
	} {
		if IsValidGitSHA(value) {
			t.Fatalf("expected %q to be invalid", value)
		}
	}
}

func TestValidateRelease(t *testing.T) {
	original := GitSHA
	t.Cleanup(func() { GitSHA = original })

	valid := "0123456789abcdef0123456789abcdef01234567"
	GitSHA = valid

	if err := ValidateRelease(valid); err != nil {
		t.Fatalf("ValidateRelease returned error: %v", err)
	}

	for _, configured := range []string{
		"dev",
		"0123456789abcdef0123456789abcdef01234568",
	} {
		if err := ValidateRelease(configured); err == nil {
			t.Fatalf("expected %q to be rejected", configured)
		}
	}

	GitSHA = "unknown"
	if err := ValidateRelease(valid); err == nil {
		t.Fatal("expected an invalid embedded Git SHA to be rejected")
	}
}
