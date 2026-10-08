(function () {
	"use strict";

	document.addEventListener("DOMContentLoaded", () => {
		document.querySelectorAll("[data-fuel-reading-row]").forEach((row) => {
			const previous = row.querySelector("[data-previous-shift-reading]");
			const current = row.querySelector("[data-reading-current]");
			const difference = row.querySelector("[data-reading-difference]");
			if (!previous || !current || !difference) return;

			const updateDifference = () => {
				const previousValue = Number.parseFloat(previous.value);
				const currentValue = Number.parseFloat(current.value);
				difference.value = Number.isFinite(previousValue) && Number.isFinite(currentValue)
					? (previousValue - currentValue).toFixed(2)
					: "";
			};
			current.addEventListener("input", updateDifference);
			updateDifference();
		});
	});
})();