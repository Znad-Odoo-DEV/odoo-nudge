{
    "name": "Nudge: Website Builder Real Estate Listings",
    "summary": "A page of property listings with filters: for sale or rent, type, city, bedrooms and price",
    "description": """
Website Builder Real Estate Listings
====================================
A plugin of the Website Builder. It adds a kind of content, "listing", that shows
the projects of the site which are for sale or for rent, with a search bar over
them (for sale or rent, property type, city, bedrooms, price from and up to).
The bar opens /listings, a public page that filters the published listings of the
website on the server: nothing is sent to anybody and no script runs.

- Listings are the project records of the site (price, area, bedrooms, status, pictures).
  Nothing is invented: a listing exists only when you wrote it; with none, the section is not placed.
- A template, "Real estate with search filters", uses it on the home page and the projects page.
- Site Check looks at the listings: a price, a city and a picture are missing.

Installed from Apps by an administrator who wants it, with Website Builder.
""",
    "category": "Website",
    "version": "18.0.1.0.0",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "id7sp22@gmail.com",
    "license": "OPL-1",
    "depends": ["nudge_builder"],
    "data": [
        "views/listings_templates.xml",
    ],
    "auto_install": False,
    "installable": True,
    "application": False,
}
