function initOfferTypeToggles(context) {
  const inlines = (context || document).querySelectorAll("div.inline-related");
  inlines.forEach((inline) => {
    const select = inline.querySelector("select[id$='offer_type']");
    if (!select) return;

    const fields = ["hours", "classes", "students", "instructors", "level"];

    function toggleFields() {
      const val = select.value;
      fields.forEach((f) => {
        const field = inline.querySelector(`[id$=${f}]`)?.closest(".form-row");
        if (!field) return;
        if (val === "lesson") {
          field.style.display = ["hours", "students", "instructors"].includes(f)
            ? ""
            : "none";
        } else if (val === "course") {
          field.style.display = ["classes", "level"].includes(f) ? "" : "none";
        } else if (val === "experience") {
          field.style.display = ["level"].includes(f) ? "" : "none";
        } else {
          field.style.display = "none";
        }
      });
    }

    select.addEventListener("change", toggleFields);
    toggleFields();
  });
}

// Ejecutar al cargar
document.addEventListener("DOMContentLoaded", function () {
  initOfferTypeToggles(document);

  // Detectar cuando nested_admin agrega nuevos inlines dinámicamente
  document.body.addEventListener("formset:added", function (event) {
    const newForm = event.detail && event.detail.formset ? event.detail.formset.$form.get(0) : event.target;
    initOfferTypeToggles(newForm);
  });
});