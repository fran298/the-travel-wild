function initOfferTypeFields(context) {
  const inlines = (context || document).querySelectorAll("div.inline-related, .inline-group");
  inlines.forEach((inline) => {
    const select = inline.querySelector("select[id$='offer_type']");
    if (!select) return;

    const fields = ["hours", "classes", "students", "instructors", "level"];

    function toggleOfferFields() {
      const val = select.value?.toLowerCase();
      fields.forEach((f) => {
        const field =
          inline.querySelector(`[id$=${f}]`)?.closest(`.form-row, .field-${f}`) ||
          inline.querySelector(`.field-${f}`);
        if (!field) return;

        switch (val) {
          case "lesson":
            field.style.display = ["hours", "students", "instructors"].includes(f) ? "" : "none";
            break;
          case "course":
            field.style.display = ["classes", "level"].includes(f) ? "" : "none";
            break;
          case "experience":
            field.style.display = ["level"].includes(f) ? "" : "none";
            break;
          case "rental":
            field.style.display = "none";
            break;
          default:
            field.style.display = ""; // fallback: show all
        }
      });
    }

    select.addEventListener("change", toggleOfferFields);
    toggleOfferFields();
  });
}

document.addEventListener("DOMContentLoaded", function () {
  console.log("✅ offers_dynamic.js loaded and ready");
  initOfferTypeFields(document);

  // Reinitialize for nested_admin when new inline forms are added dynamically
  document.body.addEventListener("formset:added", function (event) {
    const newForm =
      event.detail && event.detail.formset ? event.detail.formset.$form.get(0) : event.target;
    initOfferTypeFields(newForm);
  });
});