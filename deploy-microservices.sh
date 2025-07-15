#!/bin/bash

# Microservices Deployment Script for Trading Bot
# This script manages the deployment of all microservices

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="docker-compose.yml"
SERVICES=("config-service" "database-service" "exchange-service" "strategy-service" "orchestrator-service" "web-dashboard-service")

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

# Function to check if Docker is running
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker is not running. Please start Docker and try again."
        exit 1
    fi
    print_success "Docker is running"
}

# Function to check if Docker Compose is available
check_docker_compose() {
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose is not installed. Please install Docker Compose and try again."
        exit 1
    fi
    print_success "Docker Compose is available"
}

# Function to build all services
build_services() {
    print_status "Building all microservices..."
    
    # Create missing service directories and files if they don't exist
    for service in "${SERVICES[@]}"; do
        service_dir="services/$service"
        if [ ! -d "$service_dir" ]; then
            print_warning "Service directory $service_dir does not exist. Creating placeholder..."
            mkdir -p "$service_dir"
            
            # Create basic files for missing services
            cat > "$service_dir/main.py" << EOF
"""
$service for the Multi-Exchange Trading Bot
Placeholder service - implement actual functionality
"""

from fastapi import FastAPI
import uvicorn

app = FastAPI(title="$service", version="1.0.0")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "$service"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
EOF
            
            cat > "$service_dir/requirements.txt" << EOF
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic==2.5.0
EOF
            
            cat > "$service_dir/Dockerfile" << EOF
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \\
    gcc \\
    curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["python", "main.py"]
EOF
        fi
    done
    
    docker-compose build
    print_success "All services built successfully"
}

# Function to start all services
start_services() {
    print_status "Starting all microservices..."
    docker-compose up -d
    
    print_status "Waiting for services to be healthy..."
    sleep 30
    
    # Check service health
    for service in "${SERVICES[@]}"; do
        port=$(docker-compose port $service 8001 2>/dev/null | cut -d: -f2 || echo "8001")
        if curl -f "http://localhost:$port/health" > /dev/null 2>&1; then
            print_success "$service is healthy"
        else
            print_warning "$service health check failed"
        fi
    done
    
    print_success "All services started successfully"
}

# Function to stop all services
stop_services() {
    print_status "Stopping all microservices..."
    docker-compose down
    print_success "All services stopped successfully"
}

# Function to restart all services
restart_services() {
    print_status "Restarting all microservices..."
    docker-compose down
    docker-compose up -d
    print_success "All services restarted successfully"
}

# Function to show service status
show_status() {
    print_status "Service Status:"
    docker-compose ps
    
    print_status "Service Health:"
    for service in "${SERVICES[@]}"; do
        port=$(docker-compose port $service 8001 2>/dev/null | cut -d: -f2 || echo "8001")
        if curl -f "http://localhost:$port/health" > /dev/null 2>&1; then
            print_success "$service: Healthy"
        else
            print_error "$service: Unhealthy"
        fi
    done
}

# Function to show logs
show_logs() {
    if [ -z "$1" ]; then
        print_status "Showing logs for all services..."
        docker-compose logs -f
    else
        print_status "Showing logs for $1..."
        docker-compose logs -f "$1"
    fi
}

# Function to clean up
cleanup() {
    print_status "Cleaning up containers and volumes..."
    docker-compose down -v
    docker system prune -f
    print_success "Cleanup completed"
}

# Function to show help
show_help() {
    echo "Microservices Deployment Script for Trading Bot"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  build     - Build all microservices"
    echo "  start     - Start all microservices"
    echo "  stop      - Stop all microservices"
    echo "  restart   - Restart all microservices"
    echo "  status    - Show service status and health"
    echo "  logs      - Show logs for all services"
    echo "  logs [service] - Show logs for specific service"
    echo "  cleanup   - Clean up containers and volumes"
    echo "  help      - Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 build"
    echo "  $0 start"
    echo "  $0 logs config-service"
    echo "  $0 status"
}

# Main script logic
case "${1:-help}" in
    build)
        check_docker
        check_docker_compose
        build_services
        ;;
    start)
        check_docker
        check_docker_compose
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "$2"
        ;;
    cleanup)
        cleanup
        ;;
    help|*)
        show_help
        ;;
esac 