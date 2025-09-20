# write import statements
import os
import csv
import json
from dotenv import load_dotenv
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents import SearchClient, SearchIndexingBufferedSender
from azure.core.credentials import AzureKeyCredential
from openai import AzureOpenAI

# Load environment variables
load_dotenv()

# initialise the search client
endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
search_key = os.getenv("AZURE_SEARCH_KEY")
search_credential = AzureKeyCredential(search_key)

# Use the index name from environment variables
index_name = os.getenv("INDEX_NAME")

# init azure open ai client configuration
azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
azure_openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
azure_openai_embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")

# Initialize Azure OpenAI client
openai_client = AzureOpenAI(
    azure_endpoint=azure_openai_endpoint,
    api_key=azure_openai_api_key,
    api_version=azure_openai_api_version
)

# Initialize search client
search_client = SearchClient(
    endpoint=endpoint,
    index_name=index_name,
    credential=search_credential
)

# read all new CSV files (excluding already ingested customer_data.csv and policy_documents.csv)
def read_csv_file(file_path):
    """Generic function to read any CSV file"""
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            if row:  # Skip empty rows
                data.append(row)
    return data

def read_claims_history():
    return read_csv_file('data/claims_history.csv')

def read_coverage_details():
    return read_csv_file('data/coverage_details.csv')

def read_agent_contacts():
    return read_csv_file('data/agent_contacts.csv')

def read_claim_procedures():
    return read_csv_file('data/claim_procedures.csv')

def read_policy_exclusions():
    return read_csv_file('data/policy_exclusions.csv')

def read_network_providers():
    return read_csv_file('data/network_providers.csv')

# COMMENTED OUT - Functions for already ingested CSV files
def read_customer_data():
    return read_csv_file('data/customer_data.csv')

def read_policy_documents():
    return read_csv_file('data/policy_documents.csv')

# write get_embeddings function to generate embeddings of each csv row
def get_embeddings(text):
    """Generate embeddings using text-embedding-3-large model"""
    try:
        response = openai_client.embeddings.create(
            input=text,
            model="text-embedding-3-large"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None

def prepare_documents_for_csv(csv_data, csv_type, start_row_number=1):
    """
    Prepare documents for Azure AI Search index from any CSV type
    Mapping: page_chunk is csv row, page_embedding_text_3_large is embedding of row, page_number is row number
    """
    documents = []
    row_number = start_row_number
    
    for row in csv_data:
        # Create text representation based on CSV type
        if csv_type == "claims_history":
            text = f"Claim ID: {row['ClaimID']}, Policy: {row['PolicyNumber']}, Type: {row['ClaimType']}, Amount: ${row['ClaimAmount']}, Date: {row['ClaimDate']}, Status: {row['ClaimStatus']}, Description: {row['ClaimDescription']}, Approved: ${row['ApprovedAmount']}, Adjuster: {row['AdjusterName']}"
            doc_id = f"claim_{row['ClaimID']}"
        
        elif csv_type == "coverage_details":
            text = f"Policy: {row['PolicyNumber']}, Type: {row['PolicyType']}, Coverage Limit: ${row['CoverageLimit']}, Deductible: ${row['Deductible']}, Co-Pay: ${row['CoPay']}, Out of Pocket Max: ${row['OutOfPocketMax']}, Special Coverage: {row['SpecialCoverage']}, Exclusions: {row['ExclusionDetails']}, Pre-Auth Required: {row['PreAuthRequired']}"
            doc_id = f"coverage_{row['PolicyNumber']}"
        
        elif csv_type == "agent_contacts":
            text = f"Agent: {row['AgentName']}, ID: {row['AgentID']}, Specialization: {row['Specialization']}, Phone: {row['Phone']}, Email: {row['Email']}, Office: {row['OfficeLocation']}, Hours: {row['WorkingHours']}, Languages: {row['Languages']}, Level: {row['CertificationLevel']}"
            doc_id = f"agent_{row['AgentID']}"
        
        elif csv_type == "claim_procedures":
            text = f"Policy Type: {row['PolicyType']}, Claim Type: {row['ClaimType']}, Steps: {row['Step1']} -> {row['Step2']} -> {row['Step3']} -> {row['Step4']} -> {row['Step5']} -> {row['Step6']}, Timeline: {row['TimelineHours']} hours, Required Documents: {row['RequiredDocuments']}, Instructions: {row['SpecialInstructions']}"
            doc_id = f"procedure_{row['PolicyType']}_{row['ClaimType']}"
        
        elif csv_type == "policy_exclusions":
            text = f"Policy Type: {row['PolicyType']}, Exclusion Category: {row['ExclusionCategory']}, Description: {row['ExclusionDescription']}, Alternative Coverage: {row['AlternativeCoverage']}, Applicable States: {row['ApplicableStates']}, Effective Date: {row['EffectiveDate']}"
            doc_id = f"exclusion_{row['PolicyType']}_{row['ExclusionCategory'].replace(' ', '_')}"
        
        elif csv_type == "network_providers":
            text = f"Provider: {row['ProviderName']}, ID: {row['ProviderID']}, Type: {row['ProviderType']}, Specialty: {row['Specialty']}, Address: {row['Address']}, Phone: {row['Phone']}, Accepted Policies: {row['AcceptedPolicyTypes']}, Network Status: {row['InNetworkStatus']}, Rating: {row['Rating']}"
            doc_id = f"provider_{row['ProviderID']}"
        
        # COMMENTED OUT - Document preparation for already ingested CSV files
        # elif csv_type == "customer_data":
        #     text = f"Policy Number: {row['PolicyNumber']}, Customer: {row['CustomerName']}, Email: {row['Email']}, Status: {row['Status']}, Policy Type: {row['PolicyType']}, Start Date: {row['StartDate']}, End Date: {row['EndDate']}, Premium: ${row['PremiumAmount']}"
        #     doc_id = f"customer_{row['PolicyNumber']}"
        
        # elif csv_type == "policy_documents":
        #     text = f"Policy Type: {row['PolicyType']}, Required Documents: {row['RequiredDocuments']}"
        #     doc_id = f"policy_{row['PolicyType'].lower()}"
        
        else:
            print(f"Unknown CSV type: {csv_type}")
            continue
        
        # Generate embedding
        embedding = get_embeddings(text)
        
        if embedding:
            document = {
                "id": doc_id,
                "page_chunk": text,
                "page_embedding_text_3_large": embedding,
                "page_number": row_number
            }
            documents.append(document)
            row_number += 1
            print(f"Processed {csv_type}: {doc_id}")
    
    return documents, row_number

def upload_documents_to_index(documents, csv_type):
    """Upload documents to the Azure AI Search index"""
    try:
        with SearchIndexingBufferedSender(
            endpoint=endpoint,
            index_name=index_name,
            credential=search_credential
        ) as batch_client:
            batch_client.upload_documents(documents=documents)
        
        print(f"Successfully uploaded {len(documents)} documents from {csv_type} to index '{index_name}'")
        return True
    except Exception as e:
        print(f"Error uploading documents from {csv_type}: {e}")
        return False

def process_single_csv(csv_type, csv_data, row_number):
    """Process a single CSV file and upload to index"""
    print(f"\n--- Processing {csv_type.upper()} ---")
    print(f"Records to process: {len(csv_data)}")
    
    # Prepare documents for this CSV
    documents, next_row_number = prepare_documents_for_csv(csv_data, csv_type, row_number)
    print(f"Prepared {len(documents)} documents for indexing")
    
    # Upload to Azure AI Search index
    if upload_documents_to_index(documents, csv_type):
        print(f"✅ {csv_type} ingestion completed successfully!")
        return next_row_number
    else:
        print(f"❌ {csv_type} ingestion failed!")
        return row_number

def main():
    """Main function to orchestrate the data loading process"""
    print("Starting CSV data ingestion process for NEW CSV files...")
    print("Note: Skipping customer_data.csv and policy_documents.csv (already ingested)")
    
    # Track row numbers for sequential indexing
    current_row_number = 1
    
    # Define CSV files to process (excluding already ingested ones)
    csv_files_to_process = [
        ("claims_history", read_claims_history),
        ("coverage_details", read_coverage_details),
        ("agent_contacts", read_agent_contacts),
        ("claim_procedures", read_claim_procedures),
        ("policy_exclusions", read_policy_exclusions),
        ("network_providers", read_network_providers)
    ]
    
    # COMMENTED OUT - Already ingested CSV files (uncomment if you need to re-ingest)
    csv_files_to_process = [
    #     ("customer_data", read_customer_data),
    #     ("policy_documents", read_policy_documents),
         ("claims_history", read_claims_history),
         ("coverage_details", read_coverage_details),
         ("agent_contacts", read_agent_contacts),
         ("claim_procedures", read_claim_procedures),
         ("policy_exclusions", read_policy_exclusions),
         ("network_providers", read_network_providers)
     ]
    
    total_success = 0
    total_files = len(csv_files_to_process)
    
    # Process each CSV file one at a time
    for csv_type, read_function in csv_files_to_process:
        try:
            print(f"\n{'='*50}")
            print(f"Reading {csv_type}.csv...")
            csv_data = read_function()
            print(f"Loaded {len(csv_data)} records from {csv_type}.csv")
            
            # Process and upload this CSV
            current_row_number = process_single_csv(csv_type, csv_data, current_row_number)
            total_success += 1
            
            print(f"Completed {csv_type} - Moving to next CSV file...")
            
        except Exception as e:
            print(f"❌ Error processing {csv_type}: {e}")
            print(f"Continuing with next CSV file...")
    
    # Final summary
    print(f"\n{'='*50}")
    print("📊 INGESTION SUMMARY")
    print(f"{'='*50}")
    print(f"Total CSV files processed: {total_success}/{total_files}")
    print(f"Successfully ingested: {total_success}")
    print(f"Failed: {total_files - total_success}")
    
    if total_success == total_files:
        print("🎉 All CSV data ingestion completed successfully!")
    else:
        print(f"⚠️  {total_files - total_success} CSV files failed to ingest.")
    
    print(f"\nAll documents uploaded to index: '{index_name}'")

if __name__ == "__main__":
    main()