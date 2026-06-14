from frappe.model.document import Document


class DocumentChunk(Document):
    def autoname(self):
        self.name = f"{self.content_hash}-{self.vector_id}"
