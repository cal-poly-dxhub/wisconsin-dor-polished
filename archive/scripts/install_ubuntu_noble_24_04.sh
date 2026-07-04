sudo apt-get update

# Use the latest version of npm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion" 
nvm install --lts
nvm use --lts
npm install -g bun

# Install the CDK CLI
bun add -g aws-cdk
echo 'export PATH="/home/ubuntu/.bun/bin:$PATH"' >> ~/.bashrc

# Add the Docker repository and install Docker
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo groupadd docker
sudo usermod -aG docker $USER
sudo systemctl restart docker
sudo systemctl enable docker

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Additional misc. tools
sudo apt install -y zip unzip

# Move to target dir and clone repo
cd ~
git clone https://github.com/cal-poly-dxhub/wisconsin-dor-polished

echo ""
echo "  Prerequisites installed and repository cloned to ~/wisconsin-dor-polished."
echo "  Run the following commands:"
echo ""
echo "    newgrp docker"
echo "    cd ~/wisconsin-dor-polished"
echo "    bun install"
echo "    bun run deploy"
echo "    bun run first-time"
echo ""
