const invoicesData = {
            {% for inv in invoices %}
            "{{ inv.invoice_number }}": {
                id: {{ inv.invoice_id }},
                vendor: "{{ inv.vendor_name }}",
                date: "{{ inv.date_received.strftime('%Y-%m-%d') if inv.date_received else '' }}",
                total: {{ inv.total_amount or 0.0 }}
            },
            {% endfor %}
        };

        const variantsData = {
            {% for v in variants %}
            "{{ v.barcode }}": {
                id: {{ v.variant_id }},
                name: "{{ v.product.name }}"
            },
            {% endfor %}
        };

        const invoiceNumInput = document.getElementById('invoice_number');
        const vendorInput = document.getElementById('invoice_vendor');
        const dateInput = document.getElementById('invoice_date');
        const formInvoiceId = document.getElementById('form-invoice-id');
        const costHeader = document.getElementById('invoice_cost');
        const tbody = document.querySelector('#invoice-grid tbody');

        // 2. Listen for Invoice # typing or selection
        invoiceNumInput.addEventListener('input', async (e) => {
            const val = e.target.value;
            
            if (invoicesData[val]) {
                // Matches existing database invoice! Autofill headers
                formInvoiceId.value = invoicesData[val].id;
                vendorInput.value = invoicesData[val].vendor;
                dateInput.value = invoicesData[val].date;
                costHeader.innerText = "$" + invoicesData[val].total.toFixed(2);

                // Fetch line items from backend API
                try {
                    const res = await fetch(`/api/invoice/${invoicesData[val].id}`);
                    const data = await res.json();
                    
                    tbody.innerHTML = ''; // Clear grid
                    data.line_items.forEach(item => {
                        addRowToGrid(item.barcode, item.raw_item_name, item.variant_id, item.qty_invoiced, item.qty_counted, item.unit_price);
                    });
                } catch (err) {
                    console.error("Failed to fetch line items", err);
                }
            } else {
                // New invoice typed! Clear the hidden ID so backend creates a new one
                formInvoiceId.value = "";
                costHeader.innerText = "0.00";
                
                // If they cleared the box completely, clear the grid
                if (val === "") {
                    vendorInput.value = "";
                    dateInput.value = "";
                    tbody.innerHTML = ''; 
                }
            }
        });

        // 3. Handle manually adding items to the grid (for new invoices)
        document.getElementById('btn-add-item').addEventListener('click', () => {
            const searchVal = document.getElementById('item-search').value;
            const qty = document.getElementById('add-qty').value;
            
            if (variantsData[searchVal]) {
                const v = variantsData[searchVal];
                // Qty Invoiced is 0 for manually added lines
                addRowToGrid(searchVal, v.name, v.id, 0, qty, 0.00);
                
                // Reset add fields
                document.getElementById('item-search').value = '';
                document.getElementById('add-qty').value = 1;
            } else {
                alert("Please select a valid item barcode from the list.");
            }
        });

        // Helper function to build HTML rows
        function addRowToGrid(barcode, name, variantId, qtyInvoiced, qtyCounted, unitPrice) {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><input type="text" name="barcode[]" value="${barcode}" readonly style="width: 120px;"></td>
                <td><input type="text" name="raw_item_name[]" value="${name}" readonly style="width: 100%;"></td>
                <td><input type="number" name="qty_invoiced[]" value="${qtyInvoiced}" readonly style="width: 80px;"></td>
                <td><input type="number" name="qty_counted[]" value="${qtyCounted}" style="background: #eef; width: 80px;"></td>
                <td><input type="number" name="unit_price[]" value="${unitPrice}" step="0.01" style="width: 80px;"></td>
                <input type="hidden" name="variant_id[]" value="${variantId}">
                <td><button type="button" onclick="this.closest('tr').remove()">Remove</button></td>
            `;
            tbody.appendChild(tr);
        }