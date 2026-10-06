(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const dateInput = document.getElementById("business-date");
        const dateForm = document.getElementById("business-date-form");

        if (!dateInput || !dateForm) return;

        dateInput.addEventListener("change", function () {
            dateForm.requestSubmit();
        });
    });
})();