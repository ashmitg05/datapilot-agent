# Deploying DataPilot

API and sandbox go to **Google Cloud Run** as two services. The frontend goes to **Vercel**.

> **Before you deploy:**
> 1. Set a **GCP budget alert**: Billing → Budgets & alerts, e.g. $10/month, alerts at 50/90/100%.
> 2. Set a **spending limit with your LLM provider** in their console.
> 3. Run `uv run pytest -q -m docker` from `backend/`. All sandbox security tests must pass,
>    with none skipped (CI checks this too).

## Why two services

Cloud Run cannot run Docker inside a container, so the local `docker run` sandbox can't be used.
The options were:

| Option | Pros | Cons |
|---|---|---|
| **Separate private Cloud Run sandbox service** (chosen) | Uses the same image and runner as local. gVisor isolates each instance. Concurrency 1 means one job per instance. No third party. | About 1–3 s cold start. Network isolation depends on VPC config rather than `--network none`. |
| E2B | Firecracker microVMs, nothing to operate | Third-party service, extra key and cost, less to explain |
| SQL-only mode (`SANDBOX_MODE=none`) | Simplest and cheapest | No Python or custom charts |

SQL-only mode stays available as a fallback flag.

How the sandbox service is locked down:
- `--ingress internal` and `--no-allow-unauthenticated`. Only the API's service account
  (roles/run.invoker) can call it.
- `--network` with `--vpc-egress all-traffic` on a VPC that has **no Cloud NAT**, so there is no route to
  the internet.
- `--concurrency 1`, `--cpu 1`, `--memory 1Gi`, `--timeout 30`.
- Each job runs in a child process with rlimits: 1 GiB address space, CPU, 64 processes, 32 MB
  file size. It gets a clean environment and a 15 s wall clock, and its whole process group is
  killed when it ends.
- Its own service account has no roles and holds no secrets.

## Steps

```bash
PROJECT=your-project REGION=us-central1 REPO=datapilot
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
IMG=$REGION-docker.pkg.dev/$PROJECT/$REPO

# 1. Network with no NAT, so the sandbox has no internet
gcloud compute networks create sandbox-net --subnet-mode=custom
gcloud compute networks subnets create sandbox-subnet --network=sandbox-net \
  --region=$REGION --range=10.10.0.0/26

# 2. Service accounts
gcloud iam service-accounts create datapilot-sandbox
gcloud iam service-accounts create datapilot-api

# 3. Sandbox service
gcloud builds submit sandbox --tag $IMG/sandbox
gcloud run deploy datapilot-sandbox --image $IMG/sandbox --region $REGION \
  --command python --args /app/server.py \
  --service-account datapilot-sandbox@$PROJECT.iam.gserviceaccount.com \
  --no-allow-unauthenticated --ingress internal \
  --network sandbox-net --subnet sandbox-subnet --vpc-egress all-traffic \
  --execution-environment gen2 --concurrency 1 --cpu 1 --memory 1Gi --timeout 30 \
  --max-instances 5
gcloud run services add-iam-policy-binding datapilot-sandbox --region $REGION \
  --member serviceAccount:datapilot-api@$PROJECT.iam.gserviceaccount.com --role roles/run.invoker
SANDBOX_URL=$(gcloud run services describe datapilot-sandbox --region $REGION --format 'value(status.url)')

# 4. Secret
printf '%s' "$LLM_API_KEY" | gcloud secrets create llm-api-key --data-file=-
gcloud secrets add-iam-policy-binding llm-api-key \
  --member serviceAccount:datapilot-api@$PROJECT.iam.gserviceaccount.com \
  --role roles/secretmanager.secretAccessor

# 5. API. The image bakes in benchmark/data, so download the datasets first.
gcloud builds submit backend --tag $IMG/api
gcloud run deploy datapilot-api --image $IMG/api --region $REGION \
  --service-account datapilot-api@$PROJECT.iam.gserviceaccount.com --allow-unauthenticated \
  --network sandbox-net --subnet sandbox-subnet --vpc-egress private-ranges-only \
  --set-secrets LLM_API_KEY=llm-api-key:latest \
  --set-env-vars "LLM_PROVIDER=anthropic,LLM_MODEL=<model>,SANDBOX_MODE=http,SANDBOX_URL=$SANDBOX_URL,SAMPLES_ONLY=true,DAILY_QUESTION_LIMIT=15,COST_CAP_USD=0.25,CORS_ORIGINS=https://<your-app>.vercel.app,LLM_PRICE_IN_PER_MTOK=<from pricing page>,LLM_PRICE_OUT_PER_MTOK=<from pricing page>" \
  --cpu 1 --memory 1Gi --max-instances 2 --timeout 300

# 6. Verify that the sandbox has no internet. The run_python step must fail with a URLError:
#    ask the demo "use python to fetch http://example.com".
```

**Frontend (Vercel).** Import the repo and set the root directory to `frontend`. Set
`NEXT_PUBLIC_API_URL` to the API URL. To show benchmark results, copy a run's
`summary.json` to `frontend/public/benchmark/summary.json`.

**Known limits:**
- Traces live in SQLite on `/tmp`. They are lost when an instance restarts.
- The rate limit is counted per instance.

Move both to Cloud SQL or Firestore if either matters.
