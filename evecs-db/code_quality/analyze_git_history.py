import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import re
import numpy as np

# Read the CSV file
df = pd.read_csv('git_history.csv')

# Function to categorize commits
def categorize_commit(message):
    message = message.lower()
    if 'test' in message:
        return 'Testing'
    elif 'crud' in message:
        if 'event' in message:
            return 'Events CRUD'
        elif 'ticket' in message:
            return 'Tickets CRUD'
        elif 'location' in message:
            return 'Locations CRUD'
        elif 'login' in message:
            return 'Login CRUD'
        else:
            return 'Other CRUD'
    elif 'fix' in message or 'bug' in message:
        return 'Bug Fixes'
    elif 'merge' in message:
        return 'Merge Operations'
    elif 'code_quality' in message or 'cloc' in message:
        return 'Code Quality'
    else:
        return 'Other'

# Function to assess commit severity (1-5)
def assess_severity(message, changes):
    severity = 1  # default lowest severity
    
    # Increase severity based on keywords in message
    if 'fix' in message.lower() or 'bug' in message.lower():
        severity += 1
    if 'critical' in message.lower() or 'urgent' in message.lower():
        severity += 2
    if 'break' in message.lower() or 'crash' in message.lower():
        severity += 1
    
    # Increase severity based on number of files changed
    try:
        num_files = len(re.findall(r'(\d+) files? changed', changes))
        if num_files > 5:
            severity += 1
        if num_files > 10:
            severity += 1
    except:
        pass
    
    # Cap severity at 5
    return min(severity, 5)

# Create categories and severity levels
df['Category'] = df['Message'].apply(categorize_commit)
df['Severity'] = df.apply(lambda x: assess_severity(x['Message'], x['Code Changes']), axis=1)

# Set up the style
plt.style.use('default')
colors = sns.color_palette('husl', n_colors=10)

# Create pie chart (excluding 'Other' category)
fig, ax = plt.subplots(figsize=(14, 10))
category_counts = df['Category'].value_counts()
category_counts = category_counts[category_counts.index != 'Other']  # Remove 'Other' category

# Sort values to identify small slices
category_counts_sorted = category_counts.sort_values()
small_slice_threshold = 5  # percentage threshold for small slices

# Calculate percentages
total = category_counts.sum()
percentages = [(count/total)*100 for count in category_counts]

# Create wedges and texts
wedges, texts, autotexts = ax.pie(category_counts, 
                                 colors=colors,
                                 autopct='',  # Remove default percentage labels
                                 pctdistance=0.85,
                                 startangle=90)

# Create a circle at the center to create a donut chart effect
centre_circle = plt.Circle((0,0), 0.70, fc='white')
fig.gca().add_artist(centre_circle)

# Initialize the annotations
bbox_props = dict(boxstyle="square,pad=0.3", fc="w", ec="k", lw=0.72)
kw = dict(arrowprops=dict(arrowstyle="-"),
          bbox=bbox_props,
          zorder=0,
          va="center")

# Function to get annotation coordinates
def get_annotation_coordinates(angle, radius=1.2):
    angle_rad = np.deg2rad(angle)
    x = radius * np.cos(angle_rad)
    y = radius * np.sin(angle_rad)
    horizontalalignment = 'left' if x >= 0 else 'right'
    connectionstyle = f"angle,angleA=0,angleB={angle}"
    kw["arrowprops"].update({"connectionstyle": connectionstyle})
    return x, y, horizontalalignment

# Add annotations for all slices
for i, p in enumerate(wedges):
    ang = (p.theta2 - p.theta1)/2. + p.theta1
    
    # Get text label
    category = category_counts.index[i]
    percent = percentages[i]
    text = f'{category}\n{percent:.1f}%'
    
    # Calculate position
    x, y, horizontalalignment = get_annotation_coordinates(ang)
    
    # Add annotation
    ax.annotate(text,
                xy=(x/2, y/2), 
                xytext=(x, y),
                horizontalalignment=horizontalalignment,
                **kw)

plt.title('Distribution of Git Commits by Category\n(Excluding Other)', fontsize=14, pad=20)
plt.axis('equal')

# Save the pie chart
plt.savefig('commit_categories_pie.png', bbox_inches='tight', dpi=300, facecolor='white')
plt.close()

# Create severity bar chart with all levels
plt.figure(figsize=(12, 6))

# Create a DataFrame with all severity levels (1-5)
all_severities = pd.DataFrame({'Severity': range(1, 6)})
severity_counts = df['Severity'].value_counts().reindex(range(1, 6), fill_value=0)

# Create bar plot with custom colors
bars = plt.bar(range(1, 6), severity_counts, color=sns.color_palette('RdYlGn_r', n_colors=5))

# Customize the plot
plt.title('Distribution of Commit Severity', fontsize=14, pad=20)
plt.xlabel('Severity Level (1=Least Severe, 5=Most Severe)', fontsize=12)
plt.ylabel('Number of Commits', fontsize=12)
plt.xticks(range(1, 6))

# Add value labels on top of each bar
for bar in bars:
    height = bar.get_height()
    plt.text(bar.get_x() + bar.get_width()/2., height,
             f'{int(height)}',
             ha='center', va='bottom')

# Adjust layout and save
plt.tight_layout()
plt.savefig('commit_severity_bar.png', bbox_inches='tight', dpi=300)
plt.close()

print("Analysis complete! Generated 'commit_categories_pie.png' and 'commit_severity_bar.png'") 