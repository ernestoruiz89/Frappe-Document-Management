import hashlib

from frappe.model.document import Document


class DocumentPage(Document):
    def autoname(self):
        seed = f"{self.document}:{self.document_version}:{self.page_number}"
        self.name = hashlib.sha256(seed.encode("utf-8")).hexdigest()

