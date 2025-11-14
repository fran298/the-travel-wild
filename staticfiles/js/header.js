document.addEventListener("DOMContentLoaded", function () {
  const headerToggle = document.querySelector(".header-toggle");
  const mobileDrawer = document.querySelector(".mobile-drawer");
  const overlay = document.querySelector(".mobile-drawer__overlay");
  const closeBtn = document.querySelector(".mobile-drawer__close");
  const subToggle = document.querySelector(".sub-toggle");
  const subList = document.querySelector(".sub-list");

  // --- MOBILE DRAWER OPEN/CLOSE ---
  if (headerToggle && mobileDrawer) {
    headerToggle.addEventListener("click", () => {
      mobileDrawer.classList.add("is-open");
      document.body.classList.add("no-scroll");
    });
  }

  function closeDrawer() {
    mobileDrawer.classList.remove("is-open");
    document.body.classList.remove("no-scroll");
  }

  if (overlay) overlay.addEventListener("click", closeDrawer);
  if (closeBtn) closeBtn.addEventListener("click", closeDrawer);

  // --- MOBILE SUBMENU TOGGLE ---
  if (subToggle && subList) {
    subToggle.addEventListener("click", () => {
      const expanded = subToggle.getAttribute("aria-expanded") === "true";
      subToggle.setAttribute("aria-expanded", !expanded);
      subList.hidden = expanded;
    });
  }

  // --- DESKTOP SPORTS DROPDOWN ---
  const sportsNavItem = document.querySelector(".nav-item.nav-dropdown");
  const sportsLink = sportsNavItem ? sportsNavItem.querySelector(".nav-link") : null;
  const dropdown = sportsNavItem ? sportsNavItem.querySelector(".dropdown") : null;

  if (sportsNavItem && sportsLink && dropdown) {
    sportsLink.addEventListener("click", (e) => {
      e.preventDefault();
      dropdown.classList.toggle("is-open");
    });

    document.addEventListener("click", (e) => {
      if (!sportsNavItem.contains(e.target)) {
        dropdown.classList.remove("is-open");
      }
    });
  }
});