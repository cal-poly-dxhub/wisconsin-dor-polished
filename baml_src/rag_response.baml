// Data models for chat history and documents
class ChatTurn {
  query string
  answer string
}

class Document {
  document_id string
  title string
  content string
  source string?
  source_id string?
}

// Response structure that includes both answer and relevant document IDs
class RAGResponse {
  answer string @description("The conversational answer to the user's question, formatted as Markdown")
  relevant_document_ids string[] @description("List of document_id values for documents that were directly relevant and used to answer the question")
}

// RAG Response Generation Function
function GenerateRAGResponse(
  history: ChatTurn[],
  documents: Document[],
  query: string
) -> RAGResponse {
  client ClaudeSonnetBedrock

  prompt #"
    Prior conversation history: {{ history }}. Documents (including FAQs): {{ documents }}. User query: {{ query }}.

    CORE RESPONSIBILITIES: You are a helpful assistant that is familiar with the Wisconsin Department of Revenue. Answer questions about Department of Revenue using the documents provided. Be warm, professional, and supportive in all interactions. Maintain conversation context across multiple exchanges

    RESPONSE GUIDELINES: Be conversational and helpful, not robotic. Provide specific details when available. Structure responses clearly with relevant details. Double-check contact information for accuracy. Suggest related resources or next steps when appropriate. Always prioritize accuracy, helpfulness, and user experience in your responses.

    Don't say phrases like 'based on the provided resources', 'according to the documents', 'the FAQ indicates', 'the materials show', or similar references to source materials. **If the question lacks sufficient detail to provide an accurate answer, ask a clear and polite follow-up question to gather the necessary information.**

    IMPORTANT: After generating your answer, identify which documents were actually relevant and used to answer the question. Return the document_id values (exactly as provided) for only those documents that directly contributed to your answer.

    Be sure to format your response as valid Markdown. Don't use any backslashes to escape Markdown formatting.

    {{ ctx.output_format }}
  "#
}

// Test case for ag land and forest relationship in WI DOR policy
test ag_land_forest_relationship {
  functions [GenerateRAGResponse]
  args {
    history []
    documents [
      {
        document_id "doc-ag-001"
        title "Agricultural Land Classification Guidelines"
        content #"
          Agricultural land in Wisconsin is classified based on its use for farming purposes.
          When agricultural land contains wooded areas or forests, special considerations apply.
          Land used primarily for agricultural purposes may include incidental forest areas,
          provided the primary use remains agricultural production. The Wisconsin Department of
          Revenue requires that land classification reflects the predominant use of the property.
        "#
        source "https://dor.wisconsin.gov/ag-classification"
        source_id "ag-guidelines"
      }
      {
        document_id "doc-forest-001"
        title "Forest Land Assessment and Taxation"
        content #"
          Forest land in Wisconsin receives special tax treatment under the Managed Forest Law (MFL)
          and Forest Crop Law (FCL). Property owners must choose between agricultural classification
          and forest land designation - land cannot be classified as both simultaneously. Forest land
          must be primarily used for growing trees for commercial timber production or conservation purposes.
        "#
        source "https://dor.wisconsin.gov/forest-taxation"
        source_id "forest-guidelines"
      }
      {
        document_id "doc-transition-001"
        title "Transitioning Between Agricultural and Forest Classifications"
        content #"
          Property owners may transition land between agricultural and forest classifications,
          but this requires formal application to the Wisconsin DNR and notification to the
          Department of Revenue. The transition affects property tax assessment and may have
          implications for use value assessment programs. Landowners should carefully consider
          their long-term land use goals before making classification changes.
        "#
        source "https://dor.wisconsin.gov/classification-transition"
        source_id "transition-guide"
      }
      {
        document_id "doc-unrelated-001"
        title "Commercial Property Assessment Methods"
        content #"
          Commercial properties in Wisconsin are assessed using income, cost, or market approaches.
          The assessor determines which approach best reflects the property's fair market value.
          Commercial properties include retail stores, office buildings, industrial facilities,
          and other business-use properties.
        "#
        source "https://dor.wisconsin.gov/commercial-assessment"
        source_id "commercial-guide"
      }
    ]
    query "How does ag land relate to forests in WI DOR policy?"
  }
}
