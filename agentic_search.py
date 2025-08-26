import os
from dotenv import load_dotenv
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

# create ai search index
def create_index(index_name):
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

    index_client = SearchIndexClient(endpoint=endpoint, credential=search_credential)
    index_client.create_or_update_index(index)
    print(f"Index '{index_name}' created or updated successfully")
    return index_client

def load_data(index_name):
    url = "https://raw.githubusercontent.com/Azure-Samples/azure-search-sample-data/refs/heads/main/nasa-e-book/earth-at-night-json/documents.json"
    documents = requests.get(url).json()

    with SearchIndexingBufferedSender(endpoint=endpoint, index_name=index_name, credential=search_credential) as client:
        client.upload_documents(documents=documents)

    print(f"Documents uploaded to index '{index_name}'")

# create knowledge agent
def create_knowledge_agent(index_client, agent_name):
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
                default_reranker_threshold=2.5 #optional, set to exclude responses with a reranker score of 2.5 or lower.
            )
        ]
    )

    index_client.create_or_update_agent(knowledge_agent)
    print(f"Knowledge agent '{agent_name}' created or updated successfully")
    return knowledge_agent

# create knowledge agent client
def create_knowledge_agent_client(knowledge_agent, index_name, agent_name):
    knowledge_agent_client = KnowledgeAgentRetrievalClient(endpoint=endpoint, credential=search_credential, index_name=index_name, agent_name=agent_name)
    return knowledge_agent_client

# create knowledge agent instructions
def create_messages_for_knowledge_agent(agent_name):
    instructions = """An Q&A agent that can answer questions about the Earth at night.
    Sources have a JSON format with a ref_id that must be cited in the answer.
    If you do not have the answer, respond with "I don't know".
    """
    messages = [
        {
            "role": "assistant",
            "content": instructions
        }
    ]
    
    return messages

# accept user query and process it using the retrieval pipeline
def init_retrieval_pipeline(knowledge_agent_client, messages, index_name):
    messages.append({
        "role": "user",
        "content": """
        Why do suburban belts display larger December brightening than urban cores even though absolute light levels are higher downtown?
        Why is the Phoenix nighttime street grid is so sharply visible from space, whereas large stretches of the interstate between midwestern cities remain comparatively dim?
        """
    })
    
    retrieval_result = knowledge_agent_client.retrieve(
        retrieval_request=KnowledgeAgentRetrievalRequest(
            messages=[KnowledgeAgentMessage(role=msg["role"], content=[KnowledgeAgentMessageTextContent(text=msg["content"])]) for msg in messages if msg["role"] != "system"],
            target_index_params=[KnowledgeAgentIndexParams(index_name=index_name, reranker_threshold=2.5)]
        )
    )

    # print the retrieval result
    # print("Response--------------------------------")
    # print(textwrap.fill(retrieval_result.response[0].content[0].text, width=120))

    # print("Activity--------------------------------")
    # print(json.dumps([a.as_dict() for a in retrieval_result.activity], indent=2))

    # print("Results--------------------------------")
    # print(json.dumps([r.as_dict() for r in retrieval_result.references], indent=2))

    messages.append({
        "role": "assistant",
        "content": retrieval_result.response[0].content[0].text
    })

    return messages

# create open ai client to get response from the model
def create_openai_client():
    
    client = AzureOpenAI(
        azure_endpoint = azure_openai_endpoint,
        api_version = azure_openai_api_version,
        api_key = azure_openai_api_key
    )

    return client

# generate and print the response from the model
def generate_response(openai_client, messages):
    response = openai_client.responses.create(
        model=answer_model,
        input=messages
    )
    
    wrapped = textwrap.fill(response.output_text, width=1000)
    print(wrapped)

if __name__ == "__main__":
    # create index
    index_client = create_index(index_name)
    
    # load data to the index
    load_data(index_name)
    
    # create knowledge agent
    knowledge_agent = create_knowledge_agent(index_client, agent_name)

    # create retrieval client
    knowledge_agent_client = create_knowledge_agent_client(knowledge_agent, index_name, agent_name)

    # create knowledge agent instructions
    messages = create_messages_for_knowledge_agent(agent_name)

    # accept user query and process it using the retrieval pipeline
    messages = init_retrieval_pipeline(knowledge_agent_client, messages, index_name)
    
    # create open ai client to get response from the model
    openai_client = create_openai_client()

    # generate and print the response from the model
    generate_response(openai_client, messages)




    