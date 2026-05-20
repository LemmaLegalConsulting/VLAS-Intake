deploy:
	pipecat cloud deploy --yes --organization incredible-limpet-fuchsia-927

set-secrets:
	pipecat cloud secrets set atlas-intake-secrets --file .env --organization incredible-limpet-fuchsia-927
