package contracts

import (
	"bufio"
	"regexp"
	"strings"
	"testing"
)

// Guard the embedded contract against dropping or duplicating a live endpoint.
// This uses the published, indented YAML rather than adding an API runtime
// dependency on a YAML parser. A separate structural validator checks refs and
// examples during documentation authoring.
func TestAuthOpenAPICoversImplementedOperations(t *testing.T) {
	expected := map[string]bool{
		"POST /api/v1/auth/verify-email":        false,
		"POST /api/v1/auth/resend-verification": false,
		"POST /api/v1/auth/forgot-password":    false,
		"POST /api/v1/auth/reset-password":     false,
		"GET /health/live":                     false,
		"GET /health/ready":                    false,
		"GET /version":                         false,
	}
	for _, plane := range []string{"app", "driver", "admin"} {
		prefix := "/api/v1/" + plane
		if plane != "admin" {
			expected["POST "+prefix+"/auth/register"] = false
		}
		for _, action := range []string{
			"auth/login", "auth/verify-otp", "auth/refresh", "auth/2fa/resend",
			"auth/logout", "auth/logout-all", "auth/password/change",
			"auth/2fa/enable/begin", "auth/2fa/enable/confirm",
			"auth/2fa/disable/begin", "auth/2fa/disable/confirm",
		} {
			expected["POST "+prefix+"/"+action] = false
		}
		expected["GET "+prefix+"/me"] = false
	}
	if len(expected) != 45 {
		t.Fatalf("expected route fixture count %d, want 45", len(expected))
	}

	contract := string(AuthOpenAPI())
	if !strings.HasPrefix(contract, "openapi: 3.1.0\n") {
		t.Fatal("embedded specification must declare OpenAPI 3.1")
	}
	for _, marker := range []string{
		"  securitySchemes:", "  schemas:", "  responses:",
		"    LoginOutcome:", "      oneOf:", "    TokenPair:",
		"    ErrorEnvelope:", "    RateLimit:",
	} {
		if !strings.Contains(contract, marker) {
			t.Errorf("missing reusable contract definition: %s", marker)
		}
	}
	refPattern := regexp.MustCompile(`\$ref: '#/components/(schemas|responses|headers|securitySchemes)/([A-Za-z0-9]+)'`)
	refs := refPattern.FindAllStringSubmatch(contract, -1)
	if len(refs) < 300 {
		t.Errorf("reusable OpenAPI references unexpectedly sparse: %d", len(refs))
	}
	for _, match := range refs {
		if !strings.Contains(contract, "\n    "+match[2]+":") {
			t.Errorf("unresolved OpenAPI component reference: %s", match[0])
		}
	}

	type documentedOperation struct {
		method, path, body string
	}
	var operations []documentedOperation
	var currentPath, currentMethod string
	var currentBlock strings.Builder
	finish := func() {
		if currentMethod == "" {
			return
		}
		operations = append(operations, documentedOperation{currentMethod, currentPath, currentBlock.String()})
		currentMethod = ""
		currentBlock.Reset()
	}
	scanner := bufio.NewScanner(strings.NewReader(contract))
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	inPaths := false
	for scanner.Scan() {
		line := scanner.Text()
		if line == "paths:" {
			inPaths = true
			continue
		}
		if inPaths && line == "components:" {
			finish()
			break
		}
		if !inPaths {
			continue
		}
		if strings.HasPrefix(line, "  /") && strings.HasSuffix(line, ":") {
			finish()
			currentPath = strings.TrimSuffix(strings.TrimPrefix(line, "  "), ":")
		} else if line == "    post:" || line == "    get:" {
			finish()
			currentMethod = strings.ToUpper(strings.TrimSuffix(strings.TrimSpace(line), ":"))
		} else if currentMethod != "" {
			currentBlock.WriteString(line)
			currentBlock.WriteByte('\n')
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if len(operations) != len(expected) {
		t.Errorf("documented %d operations; expected %d", len(operations), len(expected))
	}
	operationIDs := map[string]bool{}
	for _, op := range operations {
		key := op.method + " " + op.path
		if _, ok := expected[key]; !ok {
			t.Errorf("unimplemented or duplicated operation: %s", key)
			continue
		}
		if expected[key] {
			t.Errorf("duplicate operation: %s", key)
		}
		expected[key] = true
		for _, mandatory := range []string{"      tags:", "      summary:", "      description:", "      security:", "      responses:"} {
			if !strings.Contains(op.body, mandatory) {
				t.Errorf("%s missing %q", key, mandatory)
			}
		}
		if op.method == "POST" {
			for _, mandatory := range []string{"      requestBody:", "        '415':"} {
				if !strings.Contains(op.body, mandatory) {
					t.Errorf("%s missing %q", key, mandatory)
				}
			}
		} else if strings.Contains(op.body, "      requestBody:") {
			t.Errorf("GET route %s unexpectedly declares a request body", key)
		}
		if !strings.Contains(op.body, "        $ref: '#/components/responses/") {
			t.Errorf("%s lacks structured success/error responses", key)
		}
		public := !strings.HasPrefix(op.path, "/api/v1/") || strings.HasPrefix(op.path, "/api/v1/auth/")
		for _, suffix := range []string{"/auth/register", "/auth/login", "/auth/verify-otp", "/auth/refresh", "/auth/2fa/resend"} {
			if strings.HasSuffix(op.path, suffix) {
				public = true
			}
		}
		if public {
			if !strings.Contains(op.body, "      security: []") {
				t.Errorf("public route %s incorrectly requires a bearer token", key)
			}
		} else if !strings.Contains(op.body, "      - BearerAuth: []") {
			t.Errorf("protected route %s missing BearerAuth", key)
		}
		for _, line := range strings.Split(op.body, "\n") {
			if strings.HasPrefix(line, "      operationId: ") {
				id := strings.TrimPrefix(line, "      operationId: ")
				if operationIDs[id] {
					t.Errorf("duplicate operationId: %s", id)
				}
				operationIDs[id] = true
			}
		}
	}
	for key, found := range expected {
		if !found {
			t.Errorf("missing implemented operation: %s", key)
		}
	}
	if len(operationIDs) != len(expected) {
		t.Errorf("unique operationIds %d; expected %d", len(operationIDs), len(expected))
	}
}
