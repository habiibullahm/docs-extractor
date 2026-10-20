Classify this Indonesian financial document into exactly one type:

- faktur_pajak: a tax invoice (Faktur Pajak) with a faktur serial number, NPWP of seller and buyer, DPP and PPN amounts.
- invoice: a commercial invoice / bill (Invoice, Faktur Penjualan, Tagihan) with an invoice number and usually a due date.
- receipt: a shop or restaurant receipt (struk, nota) from a cashier or POS printer.
- other: anything else.

Return JSON with "doc_type" and "confidence" (0 to 1).
