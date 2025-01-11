import streamlit as st
import pandas as pd
import numpy as np
import random
import networkx as nx
from pyvis.network import Network
import tempfile
from io import BytesIO
import xlsxwriter

#############################
# 1. MAIN APP              #
#############################

def main():
    st.title("Synthetic Social Graph Generator")

    st.markdown("""
    **Instructions**:
    1. **Upload** an Excel file with columns:
       - **Name**  
       - **Handle**  (unique identifier)  
       - **Faction**  
       - **Tags**  (e.g. "#hub", "#hub_uk", "#uk", etc.)
       - **TwHandle** (optional)  
       - **TwFollowers** (desired # of followers)  
       - **TwFollowing** (desired # of followings)  

    2. **Set probabilities**:
       - Country-Specific & Global Hub  
       - Intra-Faction vs. Inter-Faction  
       - Bandwagon Scale  
       - Big-Follows-Small ratio threshold & minimum cutoff  

    3. The **app** will create:
       - A synthetic "who-follows-whom" graph
         (ensuring each user follows at least 2 others & is followed by at least 2 others).
       - *Optionally*, a PyVis **Network Diagram** (you can toggle it below).
       - A **Downloadable Excel** matrix with adjacency codes (1,2,3,0).
    """)

    # Sliders for adjusting probabilities
    hub_country_probability = st.slider(
        "Probability if target has #hub_country and user has #country",
        0.0, 1.0, 0.6, 0.05
    )
    hub_global_probability = st.slider(
        "Probability if target is a global hub (#hub)",
        0.0, 1.0, 0.5, 0.05
    )
    p_intra_faction = st.slider(
        "Probability if same Faction (and not a hub)",
        0.0, 1.0, 0.3, 0.05
    )
    p_inter_faction = st.slider(
        "Probability if different Faction (and not a hub)",
        0.0, 1.0, 0.1, 0.05
    )
    bandwagon_scale = st.slider(
        "Bandwagon Effect Scale (0 = none, higher = stronger effect)",
        0.0, 2.0, 0.5, 0.1
    )

    # Controls for Big-Follows-Small logic
    big_follow_threshold = st.slider(
        "Big-Follows-Small Ratio Threshold (U's TwFollowers / V's TwFollowers > ? => no follow)",
        1.0, 10.0, 3.0, 0.5
    )
    min_follow_cutoff = st.slider(
        "Minimum TwFollowers cutoff (if V < this, big user won't follow)",
        0, 50000, 1000, 100
    )

    # Toggle for showing the network diagram (default OFF)
    show_diagram = st.checkbox("Show Network Diagram", value=False)

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)

            required_cols = ["Name", "Handle", "Faction", "Tags", 
                             "TwHandle", "TwFollowers", "TwFollowing"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                st.error(f"Error: missing columns {missing_cols} in the uploaded file.")
                return

            st.success("File uploaded successfully! Generating Social Graph...")

            # Generate the graph
            edges, handle_to_name, personas = generate_social_graph(
                df,
                hub_country_probability,
                hub_global_probability,
                p_intra_faction,
                p_inter_faction,
                bandwagon_scale,
                big_follow_threshold,
                min_follow_cutoff
            )

            # Display Edges as a data frame
            st.write("### Final Edges (Follower -> Followed)")
            edges_df = pd.DataFrame(edges, columns=["Follower", "Followed"])
            st.dataframe(edges_df)

            # Conditionally display the network diagram (default OFF)
            if show_diagram:
                st.write("### Network Diagram (Nodes labeled by Name)")
                display_network_graph(edges, handle_to_name)

            # Finally, let user download the Excel adjacency
            st.write("### Download Excel of the Network")
            download_excel_button(personas, edges, filename="network.xlsx")

        except Exception as e:
            st.error(f"An error occurred: {e}")


#############################
# 2. GRAPH GENERATION LOGIC #
#############################

def generate_social_graph(
    df, 
    hub_country_probability=0.6,
    hub_global_probability=0.5,
    p_intra_faction=0.3, 
    p_inter_faction=0.1,
    bandwagon_scale=0.5,
    big_follow_threshold=3.0,
    min_follow_cutoff=1000
):
    """
    Generate a synthetic social graph.

    Returns:
      edges:           list of (follower_handle, followed_handle)
      handle_to_name:  dict handle -> Name
      personas:        the final list of persona dicts used
    """

    personas = df.to_dict("records")
    handle_to_name = {p["Handle"]: p["Name"] for p in personas}

    # Initialize dynamic counters
    for person in personas:
        person["F_desired"]  = int(person["TwFollowers"])
        person["f_desired"]  = int(person["TwFollowing"])
        person["F_current"]  = 0
        person["f_current"]  = 0
        person["tag_list"]   = str(person["Tags"]).lower().split()

    # For the bandwagon effect, find max TwFollowers
    max_desired_followers = max((p["TwFollowers"] for p in personas if p["TwFollowers"]>0), default=1)

    random.shuffle(personas)
    edges = []

    for U in personas:
        possible_targets = [p for p in personas if p["Handle"] != U["Handle"]]
        random.shuffle(possible_targets)

        while U["f_current"] < U["f_desired"] and possible_targets:
            V = possible_targets.pop()

            # 1) Base probability
            p = base_probability(
                U, V,
                hub_country_probability,
                hub_global_probability,
                p_intra_faction,
                p_inter_faction
            )

            # 2) Bandwagon effect (based on V)
            bandwagon_ratio = V["TwFollowers"] / max_desired_followers
            bandwagon_factor = 1 + bandwagon_scale * bandwagon_ratio
            p *= bandwagon_factor

            # 3) Big-Follows-Small logic
            ratio_uf_to_vf = (U["TwFollowers"] / max(1, V["TwFollowers"])) if V["TwFollowers"]>0 else 99999
            is_U_big = (ratio_uf_to_vf > 1.0)

            # If ratio > threshold or V < min_follow_cutoff => p=0 if U is bigger
            if ratio_uf_to_vf > big_follow_threshold or (V["TwFollowers"] < min_follow_cutoff and is_U_big):
                p = 0.0

            # 4) If V is at or above desired follower count => reduce p
            if V["F_current"] >= V["F_desired"]:
                p *= 0.2

            # Random draw
            if random.random() < p:
                edges.append((U["Handle"], V["Handle"]))
                U["f_current"] += 1
                V["F_current"] += 1

    # Ensure min=2
    edges = ensure_minimum_two(personas, edges)

    return edges, handle_to_name, personas


def base_probability(U, V, 
                     hub_country_probability,
                     hub_global_probability, 
                     p_intra_faction,
                     p_inter_faction):
    """
    Determine the base probability that U follows V (faction/hub logic).
    """
    hub_country_tag = find_country_hub_tag(V["tag_list"])
    if hub_country_tag is not None:
        if f"#{hub_country_tag}" in U["tag_list"]:
            return hub_country_probability

    if "#hub" in V["tag_list"]:
        return hub_global_probability

    if U["Faction"] == V["Faction"]:
        return p_intra_faction

    return p_inter_faction


def find_country_hub_tag(tag_list):
    """
    Look for '#hub_xxx' in V's tags. Return 'xxx' if found, else None.
    """
    for tag in tag_list:
        if tag.startswith("#hub_") and len(tag) > 5:
            return tag.split("_", 1)[1]
    return None


#############################
# 3. FINAL FIX LOGIC        #
#############################

def ensure_minimum_two(personas, edges):
    """
    Ensure each persona has at least 2 followers & 2 following.
    """
    n = len(personas)
    handle_to_index = {p["Handle"]: i for i, p in enumerate(personas)}

    out_edges = [set() for _ in range(n)]
    in_edges  = [set() for _ in range(n)]

    for (u, v) in edges:
        if u in handle_to_index and v in handle_to_index:
            ui = handle_to_index[u]
            vi = handle_to_index[v]
            out_edges[ui].add(vi)
            in_edges[vi].add(ui)

    max_tries = 1000
    tries = 0

    while tries < max_tries:
        tries += 1
        changed = False

        for i, p in enumerate(personas):
            f_count = len(out_edges[i])
            F_count = len(in_edges[i])

            # Need >= 2 following
            if f_count < 2:
                potential_follow_backs = [
                    x for x in in_edges[i] if i not in out_edges[x]
                ]
                if potential_follow_backs:
                    target = random.choice(potential_follow_backs)
                    out_edges[i].add(target)
                    in_edges[target].add(i)
                    changed = True
                else:
                    all_indices = [idx for idx in range(n) if idx != i]
                    random_target = random.choice(all_indices)
                    if random_target not in out_edges[i]:
                        out_edges[i].add(random_target)
                        in_edges[random_target].add(i)
                        changed = True

            # Need >= 2 followers
            if F_count < 2:
                potential_followers = [
                    x for x in out_edges[i] if i not in out_edges[x]
                ]
                if potential_followers:
                    target = random.choice(potential_followers)
                    out_edges[target].add(i)
                    in_edges[i].add(target)
                    changed = True
                else:
                    all_indices = [idx for idx in range(n) if idx != i]
                    random_user = random.choice(all_indices)
                    if i not in out_edges[random_user]:
                        out_edges[random_user].add(i)
                        in_edges[i].add(random_user)
                        changed = True

        if not changed:
            break

    final_edges = []
    for ui in range(n):
        for vi in out_edges[ui]:
            final_edges.append((personas[ui]["Handle"], personas[vi]["Handle"]))
    return final_edges


#############################
# 4. NETWORK DIAGRAM        #
#############################

def display_network_graph(edges, handle_to_name):
    """
    Display the network using PyVis inside Streamlit.
    Node size is scaled by number of incoming edges (in-degree).
    Label each node by its Name, not its Handle.
    """
    # Build a NetworkX graph for analysis
    G = nx.DiGraph()
    for (follower, followed) in edges:
        G.add_node(follower)
        G.add_node(followed)
        G.add_edge(follower, followed)

    # Create a PyVis network, big height for clarity
    net = Network(height="1200px", width="100%", directed=True, bgcolor="#222222", font_color="white")

    # Let the layout run, then stabilize (stop shaking):
    net.set_options('''
    {
      "configure": {
        "enabled": true,
        "filter": ["physics"]
      },
      "physics": {
        "enabled": true,
        "solver": "repulsion",
        "repulsion": {
          "centralGravity": 0,
          "springLength": 240,
          "springConstant": 0.42,
          "nodeDistance": 225,
          "damping": 1
        },
        "maxVelocity": 50,
        "minVelocity": 0.75,
        "timestep": 0.28
      }
    }
    ''')
    

    # Calculate in-degree and add nodes
    in_degs = dict(G.in_degree())
    for node in G.nodes():
        in_degree_val = in_degs[node]
        base_size = 10
        scale_factor = 3
        node_size = base_size + scale_factor * in_degree_val

        name_label = handle_to_name.get(node, node)
        tooltip_text = f"{name_label}\nFollowers (in-degree): {in_degree_val}"

        net.add_node(
            node, 
            label=name_label,
            size=node_size,
            title=tooltip_text
        )

    # Add edges
    for edge in G.edges():
        net.add_edge(edge[0], edge[1])

    # Save to HTML and display in Streamlit
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_file:
        net.save_graph(tmp_file.name)
        tmp_file.seek(0)
        html_data = tmp_file.read().decode("utf-8")

    st.components.v1.html(html_data, height=1200, width=1200, scrolling=True)


#############################
# 5. EXCEL EXPORT           #
#############################

def create_downloadable_excel(personas, edges):
    """
    Creates an Excel file in memory with the format:
      - Row 1, Col E+ => node Handles
      - Row 2, Col E+ => node Factions
      - Row 2, Col A-D => "Persona", "Handle", "Social Handle", "Faction"
      - Row i+3 => each node's data in Col A-D
      - Intersecting cells => 1,2,3,0 adjacency codes
    Returns the raw binary of the Excel file.
    """
    n = len(personas)
    handle_to_index = {}
    index_to_handle = []
    index_to_faction = []
    index_to_name = []
    index_to_tw = []

    for idx, p in enumerate(personas):
        handle_to_index[p["Handle"]] = idx
        index_to_handle.append(p["Handle"])
        index_to_faction.append(p["Faction"])
        index_to_name.append(p["Name"])
        index_to_tw.append(p.get("TwHandle", ""))

    # Build out_edges array
    out_edges = [set() for _ in range(n)]
    for (follower, followed) in edges:
        if follower in handle_to_index and followed in handle_to_index:
            u = handle_to_index[follower]
            v = handle_to_index[followed]
            out_edges[u].add(v)

    # Build code matrix
    code = [[0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                code[i][j] = 0
            else:
                ij = (j in out_edges[i])  # i->j
                ji = (i in out_edges[j])  # j->i
                if ij and ji:
                    code[i][j] = 2
                elif ij:
                    code[i][j] = 1
                elif ji:
                    code[i][j] = 3
                else:
                    code[i][j] = 0

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Network")

    # Row 2, cols A-D => headers
    worksheet.write(1, 0, "Persona")
    worksheet.write(1, 1, "Handle")
    worksheet.write(1, 2, "Social Handle")
    worksheet.write(1, 3, "Faction")

    # Row 1, from col E => node handles
    # Row 2, from col E => node factions
    for j in range(n):
        worksheet.write(0, 4 + j, index_to_handle[j])
        worksheet.write(1, 4 + j, index_to_faction[j])

    # Rows 3+ => each node
    for i in range(n):
        row_i = i + 2
        # A-D => name, handle, TwHandle, faction
        worksheet.write(row_i, 0, index_to_name[i])
        worksheet.write(row_i, 1, index_to_handle[i])
        worksheet.write(row_i, 2, index_to_tw[i])
        worksheet.write(row_i, 3, index_to_faction[i])

        # E+ => adjacency codes
        for j in range(n):
            col_j = 4 + j
            worksheet.write(row_i, col_j, code[i][j])

    workbook.close()
    output.seek(0)
    return output.getvalue()


def download_excel_button(personas, edges, filename="network.xlsx"):
    """
    Creates a Streamlit download button for the adjacency Excel.
    """
    excel_data = create_downloadable_excel(personas, edges)
    st.download_button(
        label="Download Excel Network",
        data=excel_data,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

#############################
# 6. RUN THE APP            #
#############################

if __name__ == "__main__":
    main()
