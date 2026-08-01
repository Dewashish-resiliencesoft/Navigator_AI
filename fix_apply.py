import re

with open("navigator/app/main.py", "r") as f:
    content = f.read()

# Remove the import
content = content.replace(
    "from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph, apply_base_url_to_yaml",
    "from navigator.knowledge.site_graph import SiteGraphError, parse_site_graph"
)

# Add apply_base_url_to_yaml
APPLY = """
def apply_base_url_to_yaml(yaml_text: str, base_url: str) -> str:
    import yaml
    from copy import deepcopy
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("invalid site graph yaml")
    data["base_url"] = base_url
    return yaml.dump(data, sort_keys=False, default_flow_style=False)
"""

if "def apply_base_url_to_yaml" not in content:
    content = content.replace(
        "class ProductDomainBody(BaseModel):",
        APPLY.strip() + "\n\nclass ProductDomainBody(BaseModel):"
    )

with open("navigator/app/main.py", "w") as f:
    f.write(content)
