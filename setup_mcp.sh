#!/bin/bash

# MCP Setup Script for Perplexity Integration
# This script sets up the Perplexity MCP for Cursor AI and the trading bot

set -e

echo "🚀 Setting up Perplexity MCP Integration..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Node.js is installed
check_nodejs() {
    print_status "Checking Node.js installation..."
    if command -v node &> /dev/null; then
        NODE_VERSION=$(node --version)
        print_success "Node.js found: $NODE_VERSION"
        
        # Check if version is 16 or higher
        NODE_MAJOR=$(echo $NODE_VERSION | cut -d'v' -f2 | cut -d'.' -f1)
        if [ "$NODE_MAJOR" -ge 16 ]; then
            print_success "Node.js version is compatible (>=16)"
        else
            print_error "Node.js version $NODE_VERSION is too old. Please install version 16 or higher."
            exit 1
        fi
    else
        print_error "Node.js is not installed. Please install Node.js version 16 or higher."
        exit 1
    fi
}

# Check if npm is installed
check_npm() {
    print_status "Checking npm installation..."
    if command -v npm &> /dev/null; then
        NPM_VERSION=$(npm --version)
        print_success "npm found: $NPM_VERSION"
    else
        print_error "npm is not installed. Please install npm."
        exit 1
    fi
}

# Install MCP server
install_mcp_server() {
    print_status "Installing Perplexity MCP server..."
    
    # Install globally
    if npm install -g @modelcontextprotocol/server-perplexity; then
        print_success "MCP server installed globally"
    else
        print_warning "Failed to install globally, trying local installation..."
        if npm install @modelcontextprotocol/server-perplexity; then
            print_success "MCP server installed locally"
        else
            print_error "Failed to install MCP server"
            exit 1
        fi
    fi
}

# Check environment variables
check_env() {
    print_status "Checking environment variables..."
    
    if [ -f ".env" ]; then
        print_success ".env file found"
        
        if grep -q "PERPLEXITY_API_KEY" .env; then
            print_success "PERPLEXITY_API_KEY found in .env"
        else
            print_warning "PERPLEXITY_API_KEY not found in .env"
            echo "Please add your Perplexity API key to the .env file:"
            echo "PERPLEXITY_API_KEY=your_api_key_here"
        fi
    else
        print_warning ".env file not found"
        echo "Please create a .env file with your Perplexity API key:"
        echo "PERPLEXITY_API_KEY=your_api_key_here"
    fi
}

# Test MCP server
test_mcp_server() {
    print_status "Testing MCP server installation..."
    
    if npx @modelcontextprotocol/server-perplexity --help &> /dev/null; then
        print_success "MCP server is working correctly"
    else
        print_error "MCP server test failed"
        exit 1
    fi
}

# Check Python dependencies
check_python_deps() {
    print_status "Checking Python dependencies..."
    
    if command -v python3 &> /dev/null; then
        print_success "Python 3 found"
        
        # Check if required packages are installed
        if python3 -c "import httpx, yaml, redis" 2>/dev/null; then
            print_success "Required Python packages are installed"
        else
            print_warning "Some Python packages are missing. Please install them:"
            echo "pip install httpx pyyaml redis"
        fi
    else
        print_error "Python 3 is not installed"
        exit 1
    fi
}

# Check Docker setup
check_docker() {
    print_status "Checking Docker setup..."
    
    if command -v docker &> /dev/null; then
        print_success "Docker found"
        
        if command -v docker-compose &> /dev/null; then
            print_success "Docker Compose found"
        else
            print_warning "Docker Compose not found. Please install it."
        fi
    else
        print_warning "Docker not found. MCP service will not be available in Docker."
    fi
}

# Create necessary directories
create_directories() {
    print_status "Creating necessary directories..."
    
    mkdir -p .cursor
    print_success "Created .cursor directory"
}

# Main setup function
main() {
    echo "=========================================="
    echo "Perplexity MCP Setup for Cursor AI"
    echo "=========================================="
    
    check_nodejs
    check_npm
    install_mcp_server
    test_mcp_server
    check_env
    check_python_deps
    check_docker
    create_directories
    
    echo ""
    echo "=========================================="
    print_success "MCP Setup Complete!"
    echo "=========================================="
    echo ""
    echo "Next steps:"
    echo "1. Add your Perplexity API key to .env file"
    echo "2. Restart Cursor AI"
    echo "3. Test MCP integration: python test_mcp_integration.py"
    echo "4. Start the MCP service: npm run mcp:start"
    echo ""
    echo "For Docker deployment:"
    echo "1. docker-compose build mcp-service"
    echo "2. docker-compose up mcp-service"
    echo ""
    echo "For more information, see mcp-setup.md"
}

# Run main function
main "$@" 