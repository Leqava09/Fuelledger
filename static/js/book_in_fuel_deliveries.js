(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        const form = document.getElementById("book-in-form");
        if (!form) return;

        const deliveryList = document.getElementById("delivery-list");
        const deliveryCount = document.getElementById("id_delivery_count");
        const addButton = document.getElementById("add-delivery");
        const tanks = JSON.parse(document.getElementById("book-in-fuel-tanks").textContent);
        const maxDeliveries = Number(form.dataset.maxDeliveries);

        function updateAddButton() {
            const count = Number(deliveryCount.value);
            addButton.disabled = count >= maxDeliveries;
            addButton.textContent = count >= maxDeliveries
                ? "Maximum of 5 deliveries"
                : "Add another delivery";
        }

        function createDelivery(deliveryNumber) {
            const section = document.createElement("section");
            section.className = "form-section delivery-section";

            const title = document.createElement("div");
            title.className = "form-section-title";
            const number = document.createElement("span");
            number.textContent = String(deliveryNumber).padStart(2, "0");
            const heading = document.createElement("h2");
            heading.textContent = `Delivery ${deliveryNumber}`;
            title.append(number, heading);
            section.appendChild(title);

            const referenceRow = document.createElement("div");
            referenceRow.className = "form-row";
            const referenceId = `id_delivery_${deliveryNumber}_truck_reference`;
            const referenceLabel = document.createElement("label");
            referenceLabel.htmlFor = referenceId;
            referenceLabel.textContent = "Truck / delivery reference";
            const referenceInput = document.createElement("input");
            referenceInput.id = referenceId;
            referenceInput.name = `delivery_${deliveryNumber}_truck_reference`;
            referenceInput.type = "text";
            referenceInput.maxLength = 50;
            referenceRow.append(referenceLabel, referenceInput);
            section.appendChild(referenceRow);

            const tableWrap = document.createElement("div");
            tableWrap.className = "table-wrap";
            const table = document.createElement("table");
            const head = document.createElement("thead");
            const headerRow = document.createElement("tr");
            ["Tank", "Product", "Qty L booked in"].forEach(function (label) {
                const cell = document.createElement("th");
                cell.textContent = label;
                headerRow.appendChild(cell);
            });
            head.appendChild(headerRow);
            table.appendChild(head);

            const body = document.createElement("tbody");
            tanks.forEach(function (tank) {
                const row = document.createElement("tr");
                const nameCell = document.createElement("td");
                nameCell.textContent = tank.name;
                const productCell = document.createElement("td");
                productCell.textContent = tank.product;
                const quantityCell = document.createElement("td");
                quantityCell.className = "quantity-cell";
                const quantityInput = document.createElement("input");
                quantityInput.type = "number";
                quantityInput.min = "0";
                quantityInput.step = "0.01";
                quantityInput.className = "quantity-input";
                quantityInput.name = `delivery_${deliveryNumber}_tank_${tank.id}_qty_litres`;
                quantityInput.id = `id_delivery_${deliveryNumber}_tank_${tank.id}_qty_litres`;
                quantityInput.setAttribute("aria-label", `Delivery ${deliveryNumber} quantity for ${tank.name}`);
                quantityCell.appendChild(quantityInput);
                row.append(nameCell, productCell, quantityCell);
                body.appendChild(row);
            });
            table.appendChild(body);
            tableWrap.appendChild(table);
            section.appendChild(tableWrap);
            return section;
        }

        addButton.addEventListener("click", function () {
            const nextNumber = Number(deliveryCount.value) + 1;
            if (nextNumber > maxDeliveries) return;
            deliveryList.appendChild(createDelivery(nextNumber));
            deliveryCount.value = String(nextNumber);
            updateAddButton();
        });

        updateAddButton();
    });
})();