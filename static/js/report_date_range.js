(function () {
	"use strict";

	const period = document.querySelector("#report-period");
	const fromDate = document.querySelector("#report-start-date");
	const toDate = document.querySelector("#report-end-date");
	if (!period || !fromDate || !toDate) return;

	const asDate = (value) => {
		const [year, month, day] = value.split("-").map(Number);
		return new Date(Date.UTC(year, month - 1, day));
	};
	const asInputValue = (value) => value.toISOString().slice(0, 10);
	const backdatedValue = () => {
		const anchor = asDate(fromDate.value);
		if (period.value === "week") anchor.setUTCDate(anchor.getUTCDate() - 7);
		else if (period.value === "month") {
			const targetMonth = anchor.getUTCMonth() - 1;
			const targetDate = new Date(Date.UTC(anchor.getUTCFullYear(), targetMonth, 1));
			const lastDay = new Date(Date.UTC(targetDate.getUTCFullYear(), targetDate.getUTCMonth() + 1, 0)).getUTCDate();
			targetDate.setUTCDate(Math.min(anchor.getUTCDate(), lastDay));
			return asInputValue(targetDate);
		} else if (period.value === "year") {
			const targetYear = anchor.getUTCFullYear() - 1;
			const lastDay = new Date(Date.UTC(targetYear, anchor.getUTCMonth() + 1, 0)).getUTCDate();
			anchor.setUTCFullYear(targetYear, anchor.getUTCMonth(), Math.min(anchor.getUTCDate(), lastDay));
		}
		return asInputValue(anchor);
	};
	const updateRange = () => {
		const manual = period.value === "manual";
		toDate.readOnly = !manual;
		if (!manual && fromDate.value) toDate.value = backdatedValue();
	};
	period.addEventListener("change", updateRange);
	fromDate.addEventListener("change", updateRange);
	updateRange();
})();