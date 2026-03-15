from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
import sys


def main(*, foundry_project_endpoint: str):
    with (
        AzureCliCredential() as credential,
        AIProjectClient(
            endpoint=foundry_project_endpoint, credential=credential
        ) as client,
    ):
        deployment = next(client.deployments.list())

        response = client.get_openai_client().responses.create(
            model=deployment.name,
            input="ping",
            max_output_tokens=200,
        )

        print(f"{deployment.name}: {response.output_text}", file=sys.stderr)


#if __name__ == "__main__":
#    from dotenv import dotenv_values
#
#    config = dotenv_values()
#    foundry_project_endpoint = config.get("AZURE_FOUNDRY_PROJECT_ENDPOINT")
#    assert foundry_project_endpoint
#
#    main(foundry_project_endpoint=foundry_project_endpoint)
