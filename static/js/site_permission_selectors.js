(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        if (!window.SelectFilter) return;

        window.gettext = window.gettext || function (message) {
            return message;
        };
        window.ngettext = window.ngettext || function (singular, plural, count) {
            return count === 1 ? singular : plural;
        };
        window.interpolate = window.interpolate || function (message, values) {
            let index = 0;
            return message.replace(/%s/g, function () {
                return values[index++];
            });
        };

        document.querySelectorAll("select.selectfilter").forEach(function (select) {
            window.SelectFilter.init(
                select.id,
                select.dataset.fieldName || select.name,
                select.dataset.isStacked === "1"
            );
        });
    });
})();