import streamlit as st
import pandas as pd
import numpy as np
import random
import networkx as nx
from pyvis.network import Network
import tempfile

#############################
# 1. STREAMLIT INTERFACE   #
#############################

def main():
    st.title("Synthetic Social Graph Generator")

    st.markdown("""
    **Instructions**:
    1. Upload an Excel file with columns:
       - **Name** (e.g., “John Doe”)  
       - **Handle** (a unique ID, e.g., “john_doe”)  
       - **Faction** (e.g., "FactionA", "FactionB")  
       - **Tags** (can include "#hub" if this account is a hub)  
       - **TwHandle** (Twitter handle, optional)  
       - **TwFollowers** (the approximate number of followers this user should have)  
       - **TwFollowing** (the approximate number of accounts this user should follow)  

    2. Set the probabilities and generate the network:
       - If the target is a **hub** (“#hub” in Tags), use the **Hub Probability**.
       - Otherwise, if the target shares the same Faction, use **Intra-Faction Probability**.
       - Otherwise, use **Inter-Faction Probability**.

    3. The app will create a synthetic "who-follows-whom" graph that tries to match each user’s follower/following count.  

    4. Scroll down to see a table of edges and an interactive network diagram.
    """)

    # Sliders for adjusting probabilities
    hub_probability = st.slider("Probability if target is a hub (#hub)", 0.0, 1.0, 0.5, 0.05)
    p_intra_faction = st.slider("Probability if same Faction", 0.0, 1.0, 0.3, 0.05)
    p_inter_faction = st.slider("Probability if different Faction", 0.0, 1.0, 0.1, 0.05)

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)

            # Verify required columns
            required_cols = ["Name", "Handle", "Faction", "Tags", 
                             "TwHandle", "TwFollowers", "TwFollowing"]
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                st.error(f"Error: missing columns {missing_cols} in the uploaded file.")
                return

            st.success("File uploaded successfully! Generating Social Graph...")

            # Generate the graph
            edges = generate_social_graph(
                df,
                hub_probability,
                p_intra_faction,
                p_inter_faction
            )

            # Display Edges as a data frame
            st.write("### Final Edges (Follower -> Followed)")
            edges_df = pd.DataFrame(edges, columns=["Follower", "Followed"])
            st.dataframe(edges_df)

            # Display a network diagram
            st.write("### Network Diagram")
            display_network_graph(edges)

        except Exception as e:
            st.error(f"An error occurred: {e}")


#############################
# 2. GRAPH GENERATION LOGIC #
#############################

def generate_social_graph(df, 
                          hub_probability=0.5,
                          p_intra_faction=0.3, 
                          p_inter_faction=0.1):
    """
    Given a DataFrame with columns:
     - Name, Handle, Faction, Tags, TwHandle, TwFollowers, TwFollowing

    Generate a synthetic social graph using a probabilistic approach:
      - If target has #hub, use hub_probability.
      - Else if same Faction, use p_intra_faction.
      - Else use p_inter_faction.

    Also tries to respect each user's TwFollowers & TwFollowing counts.
    """

    # Convert to a list of dicts for easier handling
    personas = df.to_dict("records")

    # Prepare storage for dynamic counts
    for person in personas:
        person["F_desired"]  = int(person["TwFollowers"])
        person["f_desired"]  = int(person["TwFollowing"])
        person["F_current"]  = 0  # how many followers they currently have
        person["f_current"]  = 0  # how many they currently follow
        # Split tags into a list, e.g. "#hub #media" => ["#hub", "#media"]
        person["tag_list"]   = str(person["Tags"]).lower().split()

    # Shuffle to add randomness to iteration order
    random.shuffle(personas)

    # We'll store edges as tuples: (follower_handle, followed_handle)
    edges = []

    # For each user U, pick who they follow
    for U in personas:
        # potential targets
        possible_targets = [p for p in personas if p["Handle"] != U["Handle"]]
        random.shuffle(possible_targets)

        # Keep trying until we reach f_desired
        while U["f_current"] < U["f_desired"] and possible_targets:
            V = possible_targets.pop()  # pick from the end

            # Calculate base probability
            p = base_probability(U, V, hub_probability, p_intra_faction, p_inter_faction)

            # Adjust if V is at or above its desired follower count
            if V["F_current"] >= V["F_desired"]:
                # drastically reduce p, but not to zero
                p *= 0.2
            else:
                # slight boost if V is still below desired
                shortfall_ratio = (V["F_desired"] - V["F_current"]) / max(1, V["F_desired"])
                p += p * 0.2 * shortfall_ratio

            r = random.random()
            if r < p:
                # U follows V
                edges.append((U["Handle"], V["Handle"]))
                U["f_current"]  += 1
                V["F_current"]  += 1

    return edges


def base_probability(U, V, hub_probability, p_intra_faction, p_inter_faction):
    """
    Determine the base probability that U follows V.
    Rule Priority:
     1) If V is tagged '#hub', use hub_probability.
     2) Else if U and V share the same Faction, use p_intra_faction.
     3) Otherwise, use p_inter_faction.
    """
    # Check if V is a hub
    if "#hub" in V["tag_list"]:
        return hub_probability

    # Check if same faction
    if U["Faction"] == V["Faction"]:
        return p_intra_faction
    else:
        return p_inter_faction

#############################
# 3. NETWORK DIAGRAM        #
#############################

def display_network_graph(edges):
    """
    Display the network using PyVis inside Streamlit.
    """
    # Build a NetworkX graph first
    G = nx.DiGraph()
    for (follower, followed) in edges:
        G.add_node(follower)
        G.add_node(followed)
        G.add_edge(follower, followed)

    # Convert to PyVis for interactive visualization
    net = Network(height="600px", width="100%", directed=True, bgcolor="#222222", font_color="white")
    net.toggle_physics(True)

    # Add nodes and edges
    for node in G.nodes():
        net.add_node(node, label=node)
    for edge in G.edges():
        net.add_edge(edge[0], edge[1])

    # Generate HTML in a temp file and then render
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp_file:
        net.save_graph(tmp_file.name)
        tmp_file.seek(0)
        html_data = tmp_file.read().decode("utf-8")

    st.components.v1.html(html_data, height=600, scrolling=True)

#############################
# 4. RUN THE APP            #
#############################

if __name__ == "__main__":
    main()
