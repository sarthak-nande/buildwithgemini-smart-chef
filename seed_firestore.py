# Copyright 2026 Google LLC
# Seed script for Firestore recipes collection.

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-69e8752c22fd"

db = firestore.Client(project=PROJECT_ID)

RECIPES = [
    {
        "id": "keto-avocado-chicken-salad",
        "title": "Keto Avocado Chicken Salad",
        "description": "A creamy, low-carb chicken salad packed with healthy fats and protein.",
        "prep_time_mins": 15,
        "dietary_tags": ["keto", "low-carb", "gluten-free", "high-protein"],
        "ingredients": [
            "2 cups cooked chicken breast, shredded",
            "1 ripe avocado, diced",
            "1/4 cup mayo or Greek yogurt",
            "1 tbsp lime juice",
            "1/4 cup red onion, finely chopped",
            "Salt & pepper to taste",
        ],
        "instructions": [
            "In a medium bowl, mash half the avocado with lime juice and mayo.",
            "Fold in shredded chicken, diced avocado, and red onion.",
            "Season with salt and pepper to taste.",
            "Serve on lettuce wraps or enjoy directly.",
        ],
        "calories": 420,
        "protein_g": 35,
        "carbs_g": 6,
        "fat_g": 28,
    },
    {
        "id": "mediterranean-quinoa-bowl",
        "title": "Mediterranean Quinoa Power Bowl",
        "description": "A fresh and vibrant vegetarian bowl loaded with quinoa, cucumbers, olives, and feta.",
        "prep_time_mins": 20,
        "dietary_tags": ["vegetarian", "gluten-free", "mediterranean", "high-fiber"],
        "ingredients": [
            "1 cup cooked quinoa",
            "1/2 cup diced cucumber",
            "1/2 cup cherry tomatoes, halved",
            "1/4 cup kalamata olives",
            "1/4 cup feta cheese, crumbled",
            "2 tbsp olive oil & lemon dressing",
        ],
        "instructions": [
            "Fluff cooked quinoa and place in a serving bowl.",
            "Top with cucumber, cherry tomatoes, olives, and feta.",
            "Drizzle with lemon olive oil dressing and toss lightly.",
        ],
        "calories": 380,
        "protein_g": 14,
        "carbs_g": 45,
        "fat_g": 18,
    },
    {
        "id": "high-protein-berry-pancake-bowl",
        "title": "High-Protein Berry Pancake Bowl",
        "description": "Fluffy cottage-cheese protein pancakes topped with fresh blueberries and maple syrup.",
        "prep_time_mins": 15,
        "dietary_tags": ["high-protein", "vegetarian", "breakfast"],
        "ingredients": [
            "1/2 cup cottage cheese",
            "2 eggs",
            "1/2 cup rolled oats",
            "1/2 tsp baking powder",
            "1/2 cup fresh blueberries",
            "1 tbsp maple syrup",
        ],
        "instructions": [
            "Blend cottage cheese, eggs, oats, and baking powder until smooth.",
            "Cook pancakes on a non-stick skillet on medium heat for 2-3 mins per side.",
            "Serve warm topped with blueberries and maple syrup.",
        ],
        "calories": 450,
        "protein_g": 32,
        "carbs_g": 48,
        "fat_g": 12,
    },
]


def seed():
    collection_ref = db.collection("recipes")
    print(f"Seeding Firestore collection 'recipes' in project '{PROJECT_ID}'...")
    for recipe in RECIPES:
        doc_id = recipe["id"]
        collection_ref.document(doc_id).set(recipe)
        print(f"  ✓ Seeded recipe: {recipe['title']} (ID: {doc_id})")
    print("Seeding complete!")


if __name__ == "__main__":
    seed()
