package api

import (
	"bytes"
	"embed"
	"net/http"
	"time"

	"github.com/Transmogriffy-Global-Private-Limited/evcowbbe/docs/contracts"
)

// Swagger UI 5.33.3 is vendored from swagger-api/swagger-ui (Apache-2.0).
// This avoids a runtime CDN dependency and lets release binaries serve their own
// documentation even when systemd starts them outside the source checkout.
//go:embed swagger/index.html swagger/init.js swagger/swagger-ui.css swagger/swagger-ui-bundle.js swagger/LICENSE swagger/swagger-ui-bundle.js.LICENSE.txt
var documentationAssets embed.FS

const swaggerCSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"

func isDocumentationPath(path string) bool {
	switch path {
	case "/openapi.yaml", "/swagger", "/swagger/", "/swagger/index.html", "/swagger/init.js", "/swagger/swagger-ui.css", "/swagger/swagger-ui-bundle.js", "/swagger/LICENSE", "/swagger/swagger-ui-bundle.js.LICENSE.txt":
		return true
	}
	return false
}

func serveDocumentation(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Security-Policy", swaggerCSP)
	w.Header().Set("X-Robots-Tag", "noindex, nofollow")
	if r.URL.Path == "/swagger" {
		http.Redirect(w, r, "/swagger/", http.StatusPermanentRedirect)
		return
	}

	var filename, contentType string
	var content []byte
	switch r.URL.Path {
	case "/openapi.yaml":
		filename, contentType = "auth.openapi.yaml", "application/yaml; charset=utf-8"
		content = contracts.AuthOpenAPI()
	case "/swagger/", "/swagger/index.html":
		filename, contentType = "index.html", "text/html; charset=utf-8"
	case "/swagger/init.js":
		filename, contentType = "init.js", "text/javascript; charset=utf-8"
	case "/swagger/swagger-ui.css":
		filename, contentType = "swagger-ui.css", "text/css; charset=utf-8"
	case "/swagger/swagger-ui-bundle.js":
		filename, contentType = "swagger-ui-bundle.js", "text/javascript; charset=utf-8"
	case "/swagger/LICENSE":
		filename, contentType = "LICENSE", "text/plain; charset=utf-8"
	case "/swagger/swagger-ui-bundle.js.LICENSE.txt":
		filename, contentType = "swagger-ui-bundle.js.LICENSE.txt", "text/plain; charset=utf-8"
	default:
		http.NotFound(w, r)
		return
	}
	if content == nil {
		var err error
		content, err = documentationAssets.ReadFile("swagger/" + filename)
		if err != nil {
			http.Error(w, "Documentation asset unavailable", http.StatusInternalServerError)
			return
		}
	}
	w.Header().Set("Content-Type", contentType)
	http.ServeContent(w, r, filename, time.Time{}, bytes.NewReader(content))
}
