import os
import matplotlib.pyplot as plt
import pandas as pd

# Paths relative to script directory
script_dir = os.path.dirname(os.path.abspath(__file__))
tss_path = os.path.join(script_dir, "dis.tss")
output_image = os.path.join(script_dir, "discharge_plot.png")

# Read TSS file
with open(tss_path, 'r') as f:
    lines = f.readlines()

# Parse header
num_cols = int(lines[1].strip())
col_names = []
for i in range(num_cols):
    col_names.append(lines[2 + i].strip())

# Parse data rows
data_start_line = 2 + num_cols
data = []
for line in lines[data_start_line:]:
    parts = line.strip().split()
    if len(parts) == num_cols:
        data.append([float(x) for x in parts])

# Create DataFrame
df = pd.DataFrame(data, columns=col_names)
df.set_index('timestep', inplace=True)

# Custom styling for a premium aesthetic
fig, ax = plt.subplots(figsize=(11, 5.5), dpi=200)
colors = ['#3b82f6', '#10b981', '#8b5cf6', '#ec4899', '#f59e0b']

for i, col in enumerate(df.columns):
    color = colors[i % len(colors)]
    ax.plot(df.index, df[col], label=f"Station {col}", color=color, linewidth=2.5, alpha=0.9)
    ax.fill_between(df.index, df[col], color=color, alpha=0.08)

ax.set_title("LISFLOOD Simulated River Discharge (Hydrograph)", fontsize=13, fontweight='bold', pad=15, color='#1e293b')
ax.set_xlabel("Time (Days)", fontsize=10, labelpad=8, color='#475569')
ax.set_ylabel("Discharge (m³/s)", fontsize=10, labelpad=8, color='#475569')
ax.grid(True, linestyle="--", linewidth=0.5, color='#e2e8f0', alpha=0.8)

ax.legend(frameon=True, facecolor='#ffffff', edgecolor='#e2e8f0', framealpha=0.95, loc='upper right')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_color('#cbd5e1')
ax.spines['bottom'].set_color('#cbd5e1')

plt.tight_layout()
plt.savefig(output_image, bbox_inches='tight')
print(f"SUCCESS: Plot saved to {output_image}")
