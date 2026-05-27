Louisville Planting Guide App Project Proposal 

 

Developer: Katie Etheridge Davis 

Target Audience: Louisville, KY Gardeners  

Technology Stack: Dash, Plotly, Python, Open Meteo-API 

PW: LVLplant2026! 

 

Background 

This project proposes the development of an interactive gardening application specific to Louisville and the surrounding area that provides planting recommendations based on the soil temperature, season, and location (Zone 6b-7a). This app will leverage the Open Meteo-API to retrieve accurate soil temperatures and weather data, along with algorithms informed by the Farmer’s Almanac chart of soil temperatures for each plant to guide gardeners on the appropriate timing for those plants.  

 

Problem Statement 

Louisville is in an excellent region for farming or gardening. However, unpredictably early or late frosts and unseasonably cold or hot weather conditions may slightly change the optimal timing for farmers or gardeners to plant particular flowers, herbs, or vegetables, making it hard to know when to safely plant. Even trusted sources like the Farmer’s Almanac or any variety of printed farmer’s charts and calendars may misjudge the appropriate time due to inclement and unexpected weather. This app would assist local gardeners in minimizing their risk of damaged plants and maximizing their yield based on the past week’s and upcoming week’s minimum local soil temperatures. 

 

Objectives 

Create an app using Dash that is informed by web-based weather data and a chart of individualized soil-temperatures needed by specific plants 

Integrate real-time weather data tracking the past week and predicting the upcoming week for Louisville, KY from Open Meteo-API 

Provide app users with lists of current viable options for in-ground planting based on the past week’s actual and the upcoming week’s predicted minimum soil and local temperatures 

Promote high-yielding gardens to improve local gardeners’ experience, access to healthy and sustainable food options, and the local environment 

 

Technical Approach 

Temperature Dashboard 

Soil temperatures at 6cm (about 2.3 inches, for seeds) for past and upcoming 7 days 

Soil temperatures at 18cm (about 7 inches, for transplants and starts) for past and upcoming 7 days 

Air temperatures for past and upcoming 7 days 

Interactive charts for each of the above items up to a 7-day forecast 

Risk level indicator to separate each plantinto a high, medium, or low risk 

Planting Recommendations List 

Plants separated into three buckets: high, medium, and low risk to plant at current temperatures 

Plants filterable by vegetable, flower, or herb 

 

Technical Architecture & Diagram 

 

Group 1, Grouped object 

 

Data Flow 

API Integration: retrieve soil and air temperatures, including 7 days prior and predicted, hourly from Open Meteo-API 

Data Processing: transform raw API data into user-friendly format 

Risk and Recommendation Logic: apply planting risk-level logic in relation to optimal and minimum temperatures needed to present recommended plants  

User Interface: display charts of current, prior 7 days’, and predicted upcoming 7 days’ air, soil (6cm), and soil (18cm) temperatures 

User Interaction: allow filtering for specific plant types and preferred plants 

 

Data Sources:  

Open Meteo-API (weather forecast API) 

Location: Louisville, KY, USA 

Forecast days: 7 days 

Past days: 7 days 

Hourly Weather Variables: temperate, soil temperature (6cm), and soil temperature (18cm) 

Timezone: America/New York 

Settings: fahrenheit, mph, and inches 

https://open-meteo.com/en/docs?latitude=38.2542&longitude=-85.7594&hourly=temperature_2m,soil_temperature_6cm,soil_temperature_18cm&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=America%2FNew_York&past_days=7  

Soil Temperature spreadsheet  

Compiled from multiple sources to expand number of plants with data on both minimum and optimal soil temperatures needed to germinate seeds or safely transplant a start 

Categorization of each plant into vegetable, flower, or herb 

Excel-based lookup table created using the temperatures found here: 

Mississippi Foundation for Renewable Energy  

https://www.backwoodsenergy.org/seed-germination-temperature-chart.html  

The Old Farmer’s Almanac 

https://www.almanac.com/soil-temperature-chart  

 

Recommendation and Risk Logic 

High Risk/Not Recommended: any soil temperature below the minimum needed in the prior and upcoming 7 days or any minimum daily temperature below 40 degrees Fahrenheit in the prior and upcoming 7 days; soil temperature for seeds will be read at 6cm and soil temperature for transplants will need to be 18cm 

Medium Risk/May Advise Waiting: soil temperatures for the prior and upcoming 7 days are all above minimum soil temperature, and no daily temperature below 40 degrees Fahrenheit in the prior or upcoming 7 days (otherwise high risk); soil temperature for seeds will be read at 6cm and soil temperature for transplants will need to be 18cm 

Low Risk/Recommended: soil temperatures for the prior and upcoming 7 days are all at or above optimal soil temperature, no daily temperature below 40 degrees Fahrenheit for all of the 14 days (otherwise medium risk); soil temperature for seeds will be read at 6cm and soil temperature for transplants will need to be 18cm 

 

Plant Categorization 

Vegetable: asparagus, bean, beet/beetroot, blackberry, cabbage, carrot, celery, chard, collard, cucumber, eggplant, gourds, ground cherry, leek, lettuce, melon, okra, onion, parsnip, sweet pea, southern pea, pepper, pumpkin, radish, sorghum, spinach, squash, strawberry, sweet corn, tomatillo, tomato, turnip 

Flower: cosmos, marigold, senna, sunflower, zinnia 

Herb: basil, chives, cilantro, dill, mint, mustard, oregano, parsley, sage, thyme 

 

Timeline 

Week 

Tasks 

Week 1 

Research soil planting temperatures and weather data sources, select API and environmental information source, draft the project proposal 

Week 2 

Build API extraction and transformation pipeline, store and clean data on soil temperatures for each plant define three risk levels 

Week 3 

Complete storage layer, build temperature dashboard and recommendation lists in Dash 

Week 4 

Testing and fixes 

Week 5 

Final testing, presentation, and submission 

 

Expected Outcomes 

This app combines data on local soil and air temperatures with expert knowledge of plants that can grow from seed in Zone 7a/Louisville, KY and the surrounding area to empower local gardeners to have the best garden possible. The interactive features will allow the user to filter for preferred plants and allow them to see the dashboards of information leading to the risk levels of ‘Not Recommended,’ ‘May Advise Waiting,’ and ‘Recommended.’ The cost-effective development, limited scope of the project (select plants and defined location), and 5-week timeline make this a low-stakes project that may have a positive environmental impact.  
