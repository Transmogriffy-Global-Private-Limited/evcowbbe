"use strict";

(function () {
  const target = document.getElementById("swagger-ui");
  if (typeof window.SwaggerUIBundle !== "function") {
    target.textContent = "Swagger UI failed to load. Check the browser console.";
    return;
  }
  try {
    window.evcowbbeSwagger = window.SwaggerUIBundle({
      url: "/openapi.yaml",
      dom_id: "#swagger-ui",
      presets: [window.SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
      deepLinking: true,
      displayRequestDuration: true,
      docExpansion: "list",
      defaultModelsExpandDepth: -1,
      validatorUrl: null,
      persistAuthorization: false,
      queryConfigEnabled: false
    });
  } catch (error) {
    target.textContent = "Unable to initialize Swagger UI. Check the browser console.";
    console.error("Swagger UI initialization failed", error);
  }
})();
