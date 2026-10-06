(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        document.addEventListener("formset:added", function (event) {
            const formsetName = event.detail && event.detail.formsetName;
            if (formsetName !== "tanks") return;

            const addedForm = event.target;
            const tankPanel = addedForm.closest(".grp-collapse") || addedForm.querySelector(".grp-collapse");
            if (!tankPanel || !tankPanel.classList.contains("grp-closed")) return;

            const toggle = tankPanel.querySelector(".grp-collapse-handler");
            if (toggle) toggle.click();
        });

        const shiftCount = document.getElementById("id_shift_count");
        if (shiftCount) {
            const preview = document.createElement("div");
            preview.className = "shift-name-preview";
            preview.setAttribute("aria-live", "polite");

            const heading = document.createElement("span");
            heading.className = "shift-name-heading";
            heading.textContent = "Shift names";

            const tabs = document.createElement("div");
            tabs.className = "shift-name-tabs";
            tabs.setAttribute("role", "tablist");
            tabs.setAttribute("aria-label", "Configured shift names");

            preview.append(heading, tabs);
            shiftCount.insertAdjacentElement("afterend", preview);

            function renderShiftNames() {
                const namesByCount = {
                    1: ["Day"],
                    2: ["Day", "Night"],
                    3: ["Morning", "Afternoon", "Evening"],
                };
                const names = namesByCount[Number(shiftCount.value)] || namesByCount[1];
                tabs.replaceChildren();

                names.forEach(function (name) {
                    const tab = document.createElement("span");
                    tab.className = "shift-name-tab";
                    tab.setAttribute("role", "tab");
                    tab.setAttribute("aria-selected", "true");
                    tab.setAttribute("aria-readonly", "true");
                    tab.textContent = name;
                    tabs.appendChild(tab);
                });
            }

            shiftCount.addEventListener("change", renderShiftNames);
            renderShiftNames();
        }

    });
})();