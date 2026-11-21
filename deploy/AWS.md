# Lyftr on AWS EC2

The Docker backend works on EC2. The frontend stays on Vercel. EC2's current free offering is time-limited and credit-based; it is not an indefinite free VM.

## Establish a ₹0 account plan first

For an eligible new AWS customer, the current Free account plan includes $100 in sign-up credits and opportunities to earn up to $100 more. It ends after six months or when credits are exhausted, whichever comes first. AWS says the Free account plan does not charge you unless you upgrade to a Paid plan. Keep the account on the Free plan and check its time/credit balance before launching.

Accounts created before 15 July 2025 used the older 12-month EC2 offer. As of 21 November 2026, that original eligibility period has elapsed. An EC2 "Free tier eligible" label alone does not establish that a particular account can run it at no cash cost.

The user has created a t3.small Ubuntu instance in ap-southeast-2 and verified 3/3 checks and SSH login. Active Free-plan eligibility and credit balance have not been independently observed by the agent. No AWS resources have been provisioned by the agent.

Official references:

- [AWS Free Tier](https://aws.amazon.com/free/)
- [Free and Paid account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
- [EC2 Free Tier eligibility](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)

## Instance configuration

In the EC2 console, verify that the instance and image are eligible under the active Free account plan. A `t3.small` with 2 GiB RAM is a comfortable starting size for LiteLLM, FastAPI, Docker, and Caddy if offered by the account. AWS currently lists it among the new-account eligible types. A 1 GiB `t3.micro` uses fewer resources but has less memory headroom during builds. No GPU is required because inference runs through the configured providers' APIs.

Use a free-tier-eligible Ubuntu 24.04 LTS image and a modest gp3 root disk, for example 12 GiB, with usage covered by the verified credit balance. Compute, EBS storage, public IPv4, and network use can all consume credits. Run the backend and Caddy on this VM; deploy the static frontend on Vercel.

Launch in a public subnet with an internet route and an auto-assigned public IPv4 address. Create/download an SSH key pair and keep its private key local. For the instance's security group, allow:

- TCP 22 from your current public IP only.
- TCP 80 and 443 from the internet for Caddy and certificate issuance.
- UDP 443 if you want Caddy's HTTP/3 listener.

The deployment override keeps FastAPI's port 8000 inside Docker's network. Use Ubuntu's `ubuntu` SSH username. Verify any host firewall also permits the intended traffic.

## Install and deploy

Connect to the instance using your actual key-file path and instance address:

```bash
chmod 600 /path/to/lyftr.pem
ssh -i /path/to/lyftr.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

Install Docker Engine and the Docker Compose plugin using [Docker's official Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/). Compose v2.24.4+ is required for the `!reset` override.

Copy the repository to the VM. Copy its private `.env` separately; it contains the provider keys. Keep `.env` out of the public repository and set its permissions to 600. On the VM, set:

```dotenv
DATABASE_URL=sqlite:////data/app.db
PORT=8000
ENABLE_FAILURE_SIMULATION=true
BACKEND_HOST=your-public-backend-hostname
ACME_EMAIL=your-certificate-contact-email
ALLOWED_ORIGINS=https://your-project.vercel.app
```

Point the hostname's DNS at the EC2 public IP. A free IP-based DNS hostname can also be used if it resolves correctly and allows certificate issuance. Auto-assigned EC2 addresses can change when the instance is stopped and started, so update DNS and verify HTTPS afterward.

From the repository folder on the VM:

```bash
docker compose -f docker-compose.yml -f deploy/compose.vm.yml up --build -d backend caddy
docker compose -f docker-compose.yml -f deploy/compose.vm.yml ps
curl https://your-public-backend-hostname/health
```

Caddy obtains the hostname's HTTPS certificate. SQLite uses the existing named Docker volume on the EBS-backed disk. Container restarts preserve that history; terminating the VM or deleting its disk/volume is a different operation.

Import the public repository into Vercel, use `frontend` as the project root, and set `VITE_API_BASE_URL=https://your-public-backend-hostname` before the frontend build. Ensure `ALLOWED_ORIGINS` matches the actual Vercel origin exactly. After a backend environment change, recreate its container; after a frontend API URL change, rebuild the frontend.

## Verify the public deployment

```bash
LYFTR_API_URL=https://your-public-backend-hostname python3 scripts/verify_live.py
LYFTR_API_URL=https://your-public-backend-hostname python3 scripts/verify_cache.py
```

Check the Vercel UI and CORS, all three API endpoints, both real keys, cache metadata, failure simulation, and history after a backend restart. Update README Live URLs only after observing those results. Record the public demo and verify the frontend, backend, repository, and video all open without login.
