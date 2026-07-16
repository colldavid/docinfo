# AWS ECS Deployment Walkthrough

One-time setup (~30 min). After this, deploying a new version is `docker push` + clicking "Update service".

## 1. Create an AWS account

1. Go to aws.amazon.com → Create account
2. Use your MIT email or personal email — either is fine
3. You'll need a credit card. A t3.small instance running 24/7 costs ~$15/month.
   For a demo, you can stop the instance after the meeting.
4. When asked for a support plan, pick **Basic (free)**

## 2. Install the AWS CLI

```bash
# On your Mac/Linux machine:
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip && sudo ./aws/install

# Configure with your credentials (from AWS Console → IAM → Your user → Security credentials)
aws configure
# Enter: Access Key ID, Secret Access Key, region (us-east-1), output format (json)
```

## 3. Create an ECR repository (to store your Docker image)

```bash
# Create the repo
aws ecr create-repository --repository-name docinfo --region us-east-1

# Note the repositoryUri from the output — looks like:
# 123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo
```

## 4. Build and push the Docker image

```bash
# From the project root:
# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  123456789.dkr.ecr.us-east-1.amazonaws.com

# Build (this takes 5-10 min first time — downloads Python + Node base images)
docker build -t docinfo .

# Tag and push
docker tag docinfo:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo:latest
```

## 5. Create ECS cluster + task definition

In the AWS Console:

1. Go to **ECS** → **Clusters** → **Create cluster**
   - Name: `docinfo`
   - Infrastructure: **AWS Fargate** (serverless — no EC2 to manage)

2. Go to **Task definitions** → **Create new task definition**
   - Family name: `docinfo`
   - Launch type: Fargate
   - CPU: 1 vCPU, Memory: 2 GB (enough for the embedding model)
   - Container:
     - Name: `docinfo`
     - Image URI: `123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo:latest`
     - Port: `8000`
     - Environment variables: `ANTHROPIC_API_KEY=sk-ant-...`

## 6. Create the ECS service

1. In your cluster → **Services** → **Create**
2. Launch type: Fargate
3. Task definition: `docinfo`
4. Service name: `docinfo-service`
5. Desired tasks: 1
6. Under Networking: create or select a security group with port 8000 open inbound
7. Under Load balancing: skip for now (use the public IP directly for the demo)

## 7. Get the public IP

After the service starts (2-3 min):
- ECS → Clusters → docinfo → Services → docinfo-service → Tasks → click task → Public IP

Your app is live at `http://<public-ip>:8000`

## Updating after code changes

```bash
docker build -t docinfo .
docker tag docinfo:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/docinfo:latest
# Then in ECS Console: Service → Update → Force new deployment
```

## Cost notes

- Fargate 1vCPU / 2GB: ~$0.05/hour = ~$1.20/day
- For a demo: start it the night before, stop after the meeting
- For production: add an Application Load Balancer + custom domain (~$20/month total)
