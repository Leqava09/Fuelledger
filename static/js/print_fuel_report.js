document.addEventListener("DOMContentLoaded", () => {
	const printButton = document.querySelector("[data-print-report]");
	if (printButton) {
		printButton.addEventListener("click", () => window.print());
	}
});