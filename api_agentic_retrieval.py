import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any

from azure.search.documents.indexes.models import SearchIndex, SearchField, VectorSearch, VectorSearchProfile, HnswAlgorithmConfiguration, AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters, SemanticSearch, SemanticConfiguration, SemanticPrioritizedFields, SemanticField
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents import SearchIndexingBufferedSender
import requests
from azure.search.documents.indexes.models import KnowledgeAgent, KnowledgeAgentAzureOpenAIModel, KnowledgeAgentTargetIndex, KnowledgeAgentRequestLimits, AzureOpenAIVectorizerParameters
from azure.search.documents.agent import KnowledgeAgentRetrievalClient
from azure.search.documents.agent.models import KnowledgeAgentRetrievalRequest, KnowledgeAgentMessage, KnowledgeAgentMessageTextContent, KnowledgeAgentIndexParams
import textwrap
import json
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential

# load environment variables
load_dotenv()

# initialize variables
endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_SEARCH_KEY")
azure_openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
search_credential = AzureKeyCredential(search_key)
azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_gpt_deployment = os.getenv("AZURE_OPENAI_GPT_DEPLOYMENT")
azure_openai_gpt_model = os.getenv("AZURE_OPENAI_GPT_MODEL")
azure_openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
azure_openai_embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
azure_openai_embedding_model = os.getenv("AZURE_OPENAI_EMBEDDING_MODEL")
index_name = os.getenv("INDEX_NAME")
agent_name = os.getenv("AGENT_NAME")
answer_model = os.getenv("ANSWER_MODEL")
api_version = os.getenv("API_VERSION")
max_conversation_history = int(os.getenv("MAX_CONVERSATION_HISTORY", "1"))

# Create FastAPI app
app = FastAPI(title="Agentic Search API", version="1.0.0")

# Pydantic models
class AgenticRetrievalRequest(BaseModel):
    query: str

class AgenticRetrievalResponse(BaseModel):
    response_string: str
    messages: List[Dict[str, Any]]
    activity: List[Dict[str, Any]]
    references: List[Dict[str, Any]]

# Global variables to store knowledge agent (configuration) and conversation context
knowledge_agent = None
messages = []

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy"}

# Create AI Search index
def create_index(index_name: str):
    index = SearchIndex(
        name=index_name,
        fields=[
            SearchField(name="id", type="Edm.String", key=True, filterable=True, sortable=True, facetable=True),
            SearchField(name="page_chunk", type="Edm.String", filterable=False, sortable=False, facetable=False),
            SearchField(name="page_embedding_text_3_large", type="Collection(Edm.Single)", stored=False, vector_search_dimensions=3072, vector_search_profile_name="hnsw_text_3_large"),
            SearchField(name="page_number", type="Edm.Int32", filterable=True, sortable=True, facetable=True)
        ],
        vector_search=VectorSearch(
            profiles=[VectorSearchProfile(name="hnsw_text_3_large", algorithm_configuration_name="alg", vectorizer_name="azure_openai_text_3_large")],
            algorithms=[HnswAlgorithmConfiguration(name="alg")],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="azure_openai_text_3_large",
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=azure_openai_endpoint,
                        deployment_name=azure_openai_embedding_deployment,
                        model_name=azure_openai_embedding_model
                    )
                )
            ]
        ),
        semantic_search=SemanticSearch(
            default_configuration_name="semantic_config",
            configurations=[
                SemanticConfiguration(
                    name="semantic_config",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[
                            SemanticField(field_name="page_chunk")
                        ]
                    )
                )
            ]
        )
    )

    global index_client
    index_client = SearchIndexClient(endpoint=endpoint, credential=search_credential)
    index_client.create_or_update_index(index)
    return index_client

def load_data(index_name: str):
    url = "https://raw.githubusercontent.com/Azure-Samples/azure-search-sample-data/refs/heads/main/nasa-e-book/earth-at-night-json/documents.json"
    documents = requests.get(url).json()

    with SearchIndexingBufferedSender(endpoint=endpoint, index_name=index_name, credential=search_credential) as client:
        client.upload_documents(documents=documents)

def create_knowledge_agent(index_client, agent_name: str, index_name: str):
    knowledge_agent = KnowledgeAgent(
        name=agent_name,
        models=[
            KnowledgeAgentAzureOpenAIModel(
                azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                    resource_url=azure_openai_endpoint,
                    deployment_name=azure_openai_gpt_deployment,
                    model_name=azure_openai_gpt_model
                )
            )
        ],
        target_indexes=[
            KnowledgeAgentTargetIndex(
                index_name=index_name,
                default_reranker_threshold=2.5
            )
        ],
    )

    index_client.create_or_update_agent(knowledge_agent)
    return knowledge_agent

def create_knowledge_agent_client(index_name: str, agent_name: str):
    knowledge_agent_client = KnowledgeAgentRetrievalClient(endpoint=endpoint, credential=search_credential, index_name=index_name, agent_name=agent_name)
    return knowledge_agent_client

def create_messages_for_knowledge_agent():
    instructions = """You are an expert Insurance Assistant that helps customers with their insurance claims, policy information, and coverage details.

    You have access to comprehensive insurance data including:
    - Customer policy information (Health, Auto, Home, Life, Travel insurance)
    - Claims history and processing details
    - Coverage limits, deductibles, and exclusions
    - Required documents for different claim types
    - Step-by-step claim procedures and timelines
    - Insurance agent contacts and specializations
    - Network providers (doctors, repair shops, contractors)
    - Policy exclusions and alternative coverage options

    CRITICAL CUSTOMER IDENTIFICATION RULES:
    1. ALWAYS search for and verify the exact customer record first using the provided identifiers (email, name, policy number)
    2. When an email address is provided, you MUST find the customer record that contains that EXACT email address
    3. When a customer name is provided, you MUST find the customer record with that EXACT name
    4. NEVER provide information for a different customer - if you cannot find the exact customer, say so explicitly
    5. ALWAYS quote the exact customer details (Name, Policy Number, Email, Policy Type, Status) from the found record
    6. If multiple customers match criteria, list all matching customers and ask for clarification

    RESPONSE REQUIREMENTS:
    1. Start every response by confirming the customer identity with exact details from the data
    2. Always include: Customer Name, Policy Number, Email, Policy Type, Policy Status from the actual record
    3. If asked for JSON format, structure your response accordingly
    4. If you cannot find the specific customer information, say "I cannot find a customer record with the provided details"

    ACCURACY IS CRITICAL - Never guess or provide approximate information. Always use exact data from the records."""
    messages = [
        {
            "role": "assistant",
            "content": instructions
        }
    ]
    return messages

def manage_conversation_history(messages_list):
    """Manage conversation history based on MAX_CONVERSATION_HISTORY setting"""
    global max_conversation_history
    
    # Keep system/assistant instructions (first message) and manage conversation pairs
    if len(messages_list) <= 1:
        return messages_list
    
    # First message is always the assistant instructions
    instructions = messages_list[0]
    conversation_pairs = messages_list[1:]  # user/assistant pairs
    
    # Calculate how many conversation pairs to keep (user + assistant = 2 messages per conversation)
    max_pairs = max_conversation_history
    max_messages = max_pairs * 2  # user question + assistant response = 2 messages per conversation
    
    # Keep only the most recent conversation pairs
    if len(conversation_pairs) > max_messages:
        conversation_pairs = conversation_pairs[-max_messages:]
    
    # Reconstruct messages list
    return [instructions] + conversation_pairs

def init_retrieval_pipeline(knowledge_agent_client, user_question: str, index_name: str):
    global messages
    
    # Add user question to existing conversation context
    messages.append({
        "role": "user",
        "content": user_question
    })
    
    retrieval_result = knowledge_agent_client.retrieve(
        retrieval_request=KnowledgeAgentRetrievalRequest(
            messages=[KnowledgeAgentMessage(role=msg["role"], content=[KnowledgeAgentMessageTextContent(text=msg["content"])]) for msg in messages if msg["role"] != "system"],
            target_index_params=[KnowledgeAgentIndexParams(index_name=index_name, reranker_threshold=2.0)]
        )
    )

    # Add agent's retrieval response to conversation context
    messages.append({
        "role": "assistant",
        "content": retrieval_result.response[0].content[0].text # this is not LLM generated response, instead these are semantic ranker processed results, re-ranked records by their semantic ranking score.
    })
    
    # Manage conversation history based on configuration
    messages = manage_conversation_history(messages)

    # Return messages, activity, and references
    return {
        "messages": messages,
        "response": retrieval_result.response[0].content[0].text,
        "activity": [a.as_dict() for a in retrieval_result.activity],
        "references": [r.as_dict() for r in retrieval_result.references]
    }

def create_openai_client():
    client = AzureOpenAI(
        azure_endpoint = azure_openai_endpoint,
        api_version = azure_openai_api_version,
        api_key = azure_openai_api_key
    )
    return client

def generate_response(openai_client, messages):
    response = openai_client.responses.create(
        model=answer_model,
        input=messages
    )
    return response.output_text

# API Endpoints
@app.post("/create-index")
def create_index_endpoint():
    try:
        # Use index name from environment variables
        index_client = create_index(index_name)
        return {"message": f"Index '{index_name}' created or updated successfully", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating index: {str(e)}")

@app.post("/load-data")
def load_data_endpoint():
    try:
        # Use index name from environment variables
        load_data(index_name)
        return {"message": f"Documents uploaded to index '{index_name}'", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading data: {str(e)}")



@app.post("/perform-agentic-retrieval", response_model=AgenticRetrievalResponse)
def perform_agentic_retrieval(request: AgenticRetrievalRequest) -> AgenticRetrievalResponse:
    try:
        global knowledge_agent, messages
        
        # Lazy initialization - create knowledge agent if it doesn't exist
        if knowledge_agent is None:
            # Create fresh index client
            index_client = SearchIndexClient(endpoint=endpoint, credential=search_credential)
            # Create and store knowledge agent globally
            knowledge_agent = create_knowledge_agent(index_client, agent_name, index_name)
            # Initialize conversation context with agent instructions
            messages = create_messages_for_knowledge_agent()
        
        # Use values from environment variables
        # Create fresh clients for each request
        knowledge_agent_client = create_knowledge_agent_client(index_name, agent_name)
        openai_client = create_openai_client()
        
        # Perform retrieval and update conversation context
        retrieval_data = init_retrieval_pipeline(knowledge_agent_client, request.query, index_name)
        
        # Update global messages from retrieval data
        messages = retrieval_data["messages"]
        
        # Generate final response from LLM
        final_answer = generate_response(openai_client, messages)
        
        # Return comprehensive response with all data
        return AgenticRetrievalResponse(
            response_string=final_answer,
            messages=messages,
            activity=retrieval_data["activity"],
            references=retrieval_data["references"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error performing retrieval: {str(e)}")

@app.delete("/delete-knowledge-agent")
def delete_knowledge_agent_endpoint():
    try:
        global knowledge_agent, messages
        
        # Create fresh index client
        index_client = SearchIndexClient(endpoint=endpoint, credential=search_credential)
        # Delete the knowledge agent
        index_client.delete_agent(agent_name)
        
        # Reset global variables
        knowledge_agent = None
        messages = []
        
        return {"message": f"Knowledge agent '{agent_name}' deleted successfully", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting knowledge agent: {str(e)}")

@app.delete("/delete-search-index")
def delete_search_index_endpoint():
    try:
        global knowledge_agent, messages
        
        # Create fresh index client
        index_client = SearchIndexClient(endpoint=endpoint, credential=search_credential)
        # Delete the search index
        index_client.delete_index(index_name)
        
        # Reset global variables since index is gone
        knowledge_agent = None
        messages = []
        
        return {"message": f"Index '{index_name}' deleted successfully", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting search index: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
