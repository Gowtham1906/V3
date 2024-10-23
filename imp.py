import subprocess
import hcl2
import os
import json
import boto3

def fetch_vpc_details(vpc_ids, region):
    ec2_client = boto3.client('ec2', region_name=region)
    vpc_details = {}

    response = ec2_client.describe_vpcs(VpcIds=vpc_ids)

    for vpc in response['Vpcs']:
        vpc_id = vpc['VpcId']
        cidr_block = vpc['CidrBlock']
        tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}

        # Fetch the DNS attributes dynamically
        dns_support_attr = ec2_client.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsSupport')
        enable_dns_support = dns_support_attr['EnableDnsSupport']['Value']

        dns_hostnames_attr = ec2_client.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsHostnames')
        enable_dns_hostnames = dns_hostnames_attr['EnableDnsHostnames']['Value']

        vpc_details[vpc_id] = (cidr_block, tags, enable_dns_support, enable_dns_hostnames)

    missing_vpcs = set(vpc_ids) - set(vpc_details.keys())
    if missing_vpcs:
        raise Exception(f"VPCs not found: {', '.join(missing_vpcs)}")

    return vpc_details

def read_existing_tfvars(tfvars_path):
    """Read existing terraform.tfvars file if it exists."""
    if os.path.exists(tfvars_path):
        try:
            with open(tfvars_path, 'r') as f:
                content = f.read()
                parsed = hcl2.loads(content)
                return parsed.get('imported_vpc_configs', {}), parsed.get('existing_vpc_ids', [])
        except Exception as e:
            print(f"Warning: Could not parse existing tfvars file: {e}")
    return {}, []

def create_directory_structure(base_path):
    """Create the required directory structure."""
    parent_module = os.path.join(base_path, "Parent_Module")
    child_module = os.path.join(base_path, "Child_Module")
    os.makedirs(parent_module, exist_ok=True)
    os.makedirs(child_module, exist_ok=True)
    return parent_module, child_module

def create_terraform_files(parent_module, child_module):
    """Create all necessary Terraform files."""
    # Parent Module files
    parent_main_tf = """resource "aws_vpc" "my_existing_vpc" {
    for_each = var.imported_vpc_configs
    
    cidr_block           = each.value.cidr_block
    enable_dns_support   = each.value.enable_dns_support
    enable_dns_hostnames = each.value.enable_dns_hostnames
    tags                 = each.value.tags
}"""
    
    parent_variables_tf = """variable "imported_vpc_configs" {
    description = "Imported VPC configurations"
    type = map(object({
        cidr_block           = string
        enable_dns_support   = bool
        enable_dns_hostnames = bool
        tags                 = map(string)
    }))
    default = {}
}

variable "aws_region" {
    description = "AWS region"
    type        = string
}"""
    
    # Create parent module files
    with open(os.path.join(parent_module, "main.tf"), "w") as f:
        f.write(parent_main_tf)
    with open(os.path.join(parent_module, "variables.tf"), "w") as f:
        f.write(parent_variables_tf)
    
    # Child Module files
    child_main_tf = """module "imported_vpc" {
    source = "../Parent_Module"

    imported_vpc_configs = var.imported_vpc_configs
    aws_region          = var.aws_region
}"""
    
    child_variables_tf = """variable "imported_vpc_configs" {
    description = "Imported VPC configurations"
    type = map(object({
        cidr_block           = string
        enable_dns_support   = bool
        enable_dns_hostnames = bool
        tags                 = map(string)
    }))
    default = {}
}

variable "aws_region" {
    description = "AWS region"
    type        = string
}

variable "existing_vpc_ids" {
    description = "List of existing VPC IDs to import"
    type        = list(string)
    default     = []
}"""
    
    backend_tf = """terraform {
    backend "local" {}
}"""
    
    # Create child module files
    with open(os.path.join(child_module, "main.tf"), "w") as f:
        f.write(child_main_tf)
    with open(os.path.join(child_module, "variables.tf"), "w") as f:
        f.write(child_variables_tf)
    with open(os.path.join(child_module, "backend.tf"), "w") as f:
        f.write(backend_tf)

def create_or_update_tfvars(child_module, vpc_details, region):
    """Create or update terraform.tfvars file preserving existing configurations."""
    tfvars_path = os.path.join(child_module, "terraform.tfvars")
    
    # Read existing configurations
    existing_configs, existing_vpc_ids = read_existing_tfvars(tfvars_path)
    
    # Update configurations with new VPC details
    for vpc_id, (cidr_block, tags, enable_dns_support, enable_dns_hostnames) in vpc_details.items():
        existing_configs[vpc_id] = {
            "cidr_block": cidr_block,
            "enable_dns_support": enable_dns_support,
            "enable_dns_hostnames": enable_dns_hostnames,
            "tags": tags
        }
        if vpc_id not in existing_vpc_ids:
            existing_vpc_ids.append(vpc_id)
    
    # Create the configuration string for each VPC
    vpc_configs = []
    for vid, config in existing_configs.items():
        # Format tags
        tag_lines = []
        for k, v in config['tags'].items():
            tag_lines.append(f'      {k} = "{v}"')
        formatted_tags = '\n'.join(tag_lines)
        
        vpc_config = f"""  "{vid}" = {{
    cidr_block           = "{config['cidr_block']}"
    enable_dns_support   = {str(config['enable_dns_support']).lower()}
    enable_dns_hostnames = {str(config['enable_dns_hostnames']).lower()}
    tags = {{
{formatted_tags}
    }}
  }}"""
        vpc_configs.append(vpc_config)
    
    # Join all VPC configurations
    all_vpc_configs = '\n'.join(vpc_configs)
    
    # Create final tfvars content
    tfvars_content = f"""aws_region = "{region}"

existing_vpc_ids = {json.dumps(existing_vpc_ids)}

imported_vpc_configs = {{
{all_vpc_configs}
}}"""
    
    with open(tfvars_path, "w") as f:
        f.write(tfvars_content)

def main():
    """Main function to run the script."""
    # Get the current script's directory
    base_path = os.path.dirname(os.path.abspath(__file__))
    
    # Create directory structure
    parent_module, child_module = create_directory_structure(base_path)
    
    # Create Terraform files
    create_terraform_files(parent_module, child_module)
    
    # Configuration
    region = "us-east-1"  # Change this to your desired region
    vpc_ids = [  # List your VPC IDs here
        "vpc-0e4573ffe1ccab336",
        "vpc-07bd81c8b6c7c9b6d",
        # Add more VPC IDs as needed
    ]
    
    try:
        # Fetch VPC details
        print(f"Fetching details for VPCs: {', '.join(vpc_ids)}...")
        vpc_details = fetch_vpc_details(vpc_ids, region)
        
        # Create or update tfvars
        create_or_update_tfvars(child_module, vpc_details, region)
        
        # Print out the contents of the terraform.tfvars for debugging
        with open(os.path.join(child_module, "terraform.tfvars"), "r") as f:
            print(f"terraform.tfvars content:\n{f.read()}")

        # Change to Child_Module directory
        os.chdir(child_module)
        
        # Initialize Terraform
        print("Initializing Terraform...")
        subprocess.run(['terraform', 'init'], check=True)
        
        # Import VPCs
        for vpc_id in vpc_ids:
            print(f"Importing VPC {vpc_id}...")
            import_cmd = [
                'terraform', 'import',
                f'module.imported_vpc.aws_vpc.my_existing_vpc["{vpc_id}"]',
                vpc_id
            ]
            result = subprocess.run(import_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Import Output for {vpc_id}:", result.stdout)
                print(f"Import Error for {vpc_id}:", result.stderr)
            else:
                print(f"Import successful for {vpc_id}!")
        
        # Plan and apply
        print("Planning changes...")
        subprocess.run(['terraform', 'plan'], check=True)
        
        print("Applying changes...")
        subprocess.run(['terraform', 'apply', '-auto-approve'], check=True)
        
        print("VPC imports completed successfully!")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        exit(1)
        
if __name__ == "__main__":
    main()

