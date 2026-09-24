# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import os
import pathlib
import urllib.parse
import urllib.request
import uuid

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

# Hardcoded project ID, GCS bucket name, and Memory Bank Agent Engine ID
FIRESTORE_PROJECT = "qwiklabs-gcp-01-69e8752c22fd"
BUCKET_NAME = "smart-chef-images-qwiklabs-gcp-01-69e8752c22fd"
MEMORY_BANK_ID = "42988705622786048"

db = firestore.Client(project=FIRESTORE_PROJECT)
storage_client = storage.Client(project=FIRESTORE_PROJECT)
genai_client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT, location="global")

# Read Agent Engine resource name from deployment_metadata.json if available
METADATA_PATH = pathlib.Path(__file__).parent.parent / "deployment_metadata.json"
AGENT_ENGINE_RESOURCE_NAME = None
if METADATA_PATH.exists():
    try:
        with open(METADATA_PATH) as f:
            meta = json.load(f)
            AGENT_ENGINE_RESOURCE_NAME = meta.get("remote_agent_runtime_id")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME
)


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: after each turn, send the session to Memory Bank for extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory extraction skipped: {e}")
    return None


def list_recipes(dietary_tag: str = "") -> list[dict]:
    """Search or list recipes from the Firestore database, optionally filtering by dietary tag.

    Args:
        dietary_tag: Optional dietary tag filter such as 'keto', 'vegetarian', 'high-protein', 'gluten-free'.

    Returns:
        A list of recipe dictionaries containing ID, title, description, prep_time_mins, dietary_tags, and nutritional macros.
    """
    collection_ref = db.collection("recipes")
    docs = collection_ref.stream()
    recipes = []
    tag_clean = dietary_tag.strip().lower() if dietary_tag else ""
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        tags = [t.lower() for t in data.get("dietary_tags", [])]
        if not tag_clean or tag_clean in tags:
            recipes.append(data)
    return recipes


def get_recipe(recipe_id: str) -> dict:
    """Retrieve full details of a specific recipe by ID from Firestore.

    Args:
        recipe_id: The unique ID string of the recipe (e.g. 'keto-avocado-chicken-salad').

    Returns:
        A dictionary with complete recipe details including ingredients, instructions, and macros.
    """
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return {"error": f"Recipe '{recipe_id}' not found."}


def add_recipe(
    title: str,
    description: str,
    prep_time_mins: int,
    dietary_tags: list[str],
    ingredients: list[str],
    instructions: list[str],
    calories: int,
    protein_g: int,
    carbs_g: int,
    fat_g: int,
) -> dict:
    """Add a new custom recipe to the Firestore database.

    Args:
        title: Title of the recipe.
        description: Brief summary of the dish.
        prep_time_mins: Total preparation and cooking time in minutes.
        dietary_tags: List of dietary tags (e.g. ['keto', 'high-protein']).
        ingredients: List of ingredient strings with measurements.
        instructions: Step-by-step cooking instructions.
        calories: Total calorie count per serving.
        protein_g: Grams of protein per serving.
        carbs_g: Grams of carbohydrates per serving.
        fat_g: Grams of fat per serving.

    Returns:
        A status dictionary confirming creation with the assigned recipe ID.
    """
    doc_id = title.lower().replace(" ", "-").replace("/", "-")
    recipe_data = {
        "id": doc_id,
        "title": title,
        "description": description,
        "prep_time_mins": prep_time_mins,
        "dietary_tags": dietary_tags,
        "ingredients": ingredients,
        "instructions": instructions,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }
    db.collection("recipes").document(doc_id).set(recipe_data)
    return {"status": "success", "message": f"Recipe '{title}' saved successfully.", "recipe_id": doc_id}


def calculate_recipe_scale_and_macros(recipe_id: str, target_servings: int) -> dict:
    """Calculate scaled macro totals for a recipe for a target number of servings.

    Args:
        recipe_id: The ID of the recipe to scale (e.g. 'keto-avocado-chicken-salad').
        target_servings: The desired number of servings to scale to (e.g. 4).

    Returns:
        A dictionary with scaled total calories, protein_g, carbs_g, fat_g, and target_servings.
    """
    recipe = get_recipe(recipe_id)
    if "error" in recipe:
        return recipe

    return {
        "recipe_id": recipe_id,
        "title": recipe.get("title", ""),
        "target_servings": target_servings,
        "total_calories": recipe.get("calories", 0) * target_servings,
        "total_protein_g": recipe.get("protein_g", 0) * target_servings,
        "total_carbs_g": recipe.get("carbs_g", 0) * target_servings,
        "total_fat_g": recipe.get("fat_g", 0) * target_servings,
        "per_serving": {
            "calories": recipe.get("calories", 0),
            "protein_g": recipe.get("protein_g", 0),
            "carbs_g": recipe.get("carbs_g", 0),
            "fat_g": recipe.get("fat_g", 0),
        },
    }


def search_online_recipes(query: str) -> list[dict]:
    """Search for real recipes online from TheMealDB public food API.

    Args:
        query: The meal name or ingredient search term (e.g. 'chicken', 'arrabiata', 'pasta').

    Returns:
        A list of online recipe summaries containing title, category, cuisine area, image URL, and instructions.
    """
    api_key = os.getenv("MEALDB_API_KEY", "1")  # Uses MEALDB_API_KEY env var, defaults to free test key '1'
    url = f"https://www.themealdb.com/api/json/v1/{urllib.parse.quote(api_key)}/search.php?s={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartChefAgent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            meals = data.get("meals") or []
            results = []
            for m in meals[:3]:  # Top 3 matching recipes
                results.append({
                    "id": m.get("idMeal"),
                    "title": m.get("strMeal"),
                    "category": m.get("strCategory"),
                    "cuisine": m.get("strArea"),
                    "instructions": (m.get("strInstructions") or "")[:250] + "...",
                    "image_url": m.get("strMealThumb"),
                })
            return results
    except Exception as e:
        return [{"error": f"Failed to fetch online recipes: {str(e)}"}]


async def generate_plating_image(prompt: str, tool_context: ToolContext) -> dict:
    """Generate a visual plating image for a recipe or dish using gemini-3.1-flash-lite-image model.

    Args:
        prompt: Description of the dish or meal to generate an image for (e.g. 'A beautifully plated gourmet avocado chicken salad').
        tool_context: The ADK ToolContext used to save session artifacts.

    Returns:
        A dictionary containing the public HTTPS storage URL of the uploaded image and artifact filename.
    """
    response = genai_client.models.generate_content(
        model="gemini-3.1-flash-lite-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )

    image_bytes = None
    mime_type = "image/jpeg"

    if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                image_bytes = part.inline_data.data
                mime_type = part.inline_data.mime_type or "image/jpeg"
                break

    if not image_bytes:
        return {"error": "Failed to generate image bytes."}

    ext = "png" if "png" in mime_type else "jpg"
    filename = f"recipe_{uuid.uuid4().hex[:8]}.{ext}"

    # 1. Save with tool_context.save_artifact if artifact service is initialized (e.g. adk web)
    try:
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)
    except Exception:
        pass

    # 2. Upload image bytes to public Cloud Storage bucket and return public HTTPS URL
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

    return {
        "status": "success",
        "image_url": public_url,
        "artifact_filename": filename,
        "prompt": prompt,
    }


async def generate_recipe_video(
    dish_name: str,
    description: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Generates a short cooking/plating video for a dish using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Saves the video as an artifact for the Playground and uploads it to public Cloud Storage.

    Args:
        dish_name: The name of the dish or recipe item to generate a video for.
        description: Optional details about the dish preparation or plating style.
        tool_context: ADK ToolContext provided automatically by the runtime.

    Returns:
        A dictionary containing the public HTTPS storage URL of the uploaded video and artifact filename.
    """
    prompt = f"A short video showing the cooking and plating of {dish_name}."
    if description:
        prompt += f" {description}"

    res = genai_client.interactions.create(
        model="gemini-omni-flash-preview",
        input=prompt,
    )

    if not res.output_video or not res.output_video.data:
        return {"error": "Failed to generate video data from Omni model."}

    raw_data = res.output_video.data
    mime_type = getattr(res.output_video, "mime_type", None) or "video/mp4"

    if isinstance(raw_data, str):
        video_bytes = base64.b64decode(raw_data)
    else:
        video_bytes = raw_data

    filename = f"video_{uuid.uuid4().hex[:8]}.mp4"

    # 1. Save with tool_context.save_artifact if artifact service is initialized (e.g. adk web / Playground)
    try:
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        await tool_context.save_artifact(filename=filename, artifact=artifact_part)
    except Exception:
        pass

    # 2. Upload video bytes to public Cloud Storage bucket and return public HTTPS URL
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    blob.upload_from_string(video_bytes, content_type=mime_type)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

    return {
        "status": "success",
        "video_url": public_url,
        "artifact_filename": filename,
        "prompt": prompt,
    }


def memory_bank_service_builder():
    """Service builder for Vertex AI Memory Bank integration."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


# Build A2UI v0.8 System Prompt
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description="You are Smart Chef, an intelligent personal culinary and nutrition AI assistant.",
    workflow_description=(
        "Analyze the user's culinary request, execute tools as needed, and return structured UI components when appropriate. "
        "When the user asks to generate, show, or display an image, photo, or plating view of a dish, ALWAYS call the generate_plating_image tool first. "
        "Use the returned image_url in an Image component inside the card. "
        "When the user asks to generate, show, or display a video, clip, or cooking process of a dish, ALWAYS call the generate_recipe_video tool first. "
        "Always include the returned video_url in your response (for example as plain text or link) so the video renders for the user. "
        "You automatically receive the user's saved cross-session memory facts (dietary restrictions, allergies, macro goals, pantry inventory, kitchen equipment) in your context when available."
    ),
    ui_description=(
        "The root component of every surface MUST be a single Card (e.g. id: 'root_card', component: Card). "
        "Set the child of root_card to a single Column (e.g. id: 'root_column', component: Column). "
        "Put Text elements inside root_column for titles, descriptions, ingredients, and instructions. "
        "To render an image returned by generate_plating_image or online search, include an Image component in root_column with url set to literalString of the image_url, e.g. {'id': 'dish_img', 'component': {'Image': {'url': {'literalString': 'https://storage.googleapis.com/...'}}}}. "
        "NEVER use Column as the root component, and NEVER put multiple Cards in one surface. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use Table or Heading. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in <a2a_datapart_json> tags."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        list_recipes,
        get_recipe,
        add_recipe,
        calculate_recipe_scale_and_macros,
        search_online_recipes,
        generate_plating_image,
        generate_recipe_video,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
