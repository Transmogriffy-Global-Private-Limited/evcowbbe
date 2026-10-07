package buildinfo

import (
	"fmt"
	"regexp"
)

type Info struct {
	Version   string `json:"version"`
	GitSHA    string `json:"git_sha"`
	BuildTime string `json:"build_time"`
}

var (
	Version   = "dev"
	GitSHA    = "unknown"
	BuildTime = "unknown"
)

var gitSHARegexp = regexp.MustCompile(`^[0-9a-f]{40}$`)

func Current() Info {
	return Info{
		Version:   Version,
		GitSHA:    GitSHA,
		BuildTime: BuildTime,
	}
}

// IsValidGitSHA reports whether value is an exact full lowercase Git object ID.
func IsValidGitSHA(value string) bool {
	return gitSHARegexp.MatchString(value)
}

// ValidateRelease verifies the source identity invariant for a non-development
// process: its configured intended revision must exactly match the revision
// embedded in the executable.
func ValidateRelease(configuredRevision string) error {
	if !IsValidGitSHA(GitSHA) {
		return fmt.Errorf("embedded Git SHA must be a 40-character lowercase Git SHA")
	}

	if !IsValidGitSHA(configuredRevision) {
		return fmt.Errorf("BUILD_REVISION must be a 40-character lowercase Git SHA")
	}

	if configuredRevision != GitSHA {
		return fmt.Errorf("BUILD_REVISION must equal the embedded Git SHA")
	}

	return nil
}
