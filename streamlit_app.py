import streamlit as st
import pandas as pd
import numpy as np
import random
import networkx as nx
from pyvis.network import Network
import tempfile
import os

#############################
# 1. STREAMLIT INTERFACE   #
#############################

def main():
    st.title("Synthetic Social Graph Generator")
    st.write("""
    **Instructions**:
    1. Upload an Excel file with columns: 
       - Name, Handle, Faction, Tags, TwHandle, TwFollowers, TwFollowing.
    2. Adjust the sliders below to tune the probabilities.
    3. A synthetic network of who-follows-whom will be generated.
    4. Check the Network Diagram at the bottom.
    """)

    # Sliders for adjusting probabilities
    p_intra_faction = st.slider("Probability: Intra-Faction", 0.0, 1.0, 0.4, 0.05)
    p_inter_faction = st.slider("Probability: Inter-Faction", 0.0, 1.0, 0.1, 0.05)
    p_intra_country = st.slider("Probability: Intra-Country (#latvian)", 0.0, 1.0, 0.4, 0.05)
    p_inter_country = st.slider("Probability: Inter-Country (#latvian)", 0.0, 1.0, 0.05, 0.05)
    hub_multiplier = st.slider("Hub Multiplier (#hub)", 1.0, 5.0, 2.0, 0.5)

    uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx", "xls"])
    if uploaded_file is not None:
        try:
            df = pd.read_excel(uploaded_file)

            # Verify required columns
            required_cols = ["Name", "Handle", "Faction", "Tags", 
                             "TwHandle", "TwFollowers", "TwFollowing"]
            for col in required_cols:
                if col not in df.columns:
                    st.error(f"Error: missing column '{col}' in the uploaded file.")
                    return

            st.success("File uploaded successfully! Generating Social Graph...")

            # Generate the graph
            edges = generate_social_graph(df, 
                                          p_intra_faction, 
                                          p_inter_faction,
                                          p_intra_country,
                                          p_inter_country,
                                          hub_multiplier)

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
                          p_intra_faction=0.4, 
                          p_inter_faction=0.1,
                          p_intra_country=0.4, 
                          p_inter_country=0.05,
                          hub_multiplier=2.0):
    """
    Given a DataFrame with columns:
     - Name, Handle, Faction, Tags, TwHandle, TwFollowers, TwFollowing
    Generate a synthetic social graph using a probabilistic approach.
    """

    # Convert to a list of dicts for easier handling
    personas = df.to_dict("records")

    # Prepare storage for dynamic counts
    # We track how many times each user is followed (F_current) 
    # and how many times they have followed others (f_current).
    for person in personas:
        person["F_desired"]   = int(person["TwFollowers"])
        person["f_desired"]   = int(person["TwFollowing"])
        person["F_current"]   = 0
        person["f_current"]   = 0
        person["tag_list"]    = str(person["Tags"]).lower().split()  # e.g. ["#hub", "#latvian"]

    # Shuffle to add randomness to iteration order
    random.shuffle(personas)

    # We will store edges as tuples: (follower_handle, followed_handle)
    edges = []

    # For each user, pick who they follow
    for i, U in enumerate(personas):
        possible_targets = [p for p in personas if p["Handle"] != U["Handle"]]

        # Shuffle possible targets for random picking
        random.shuffle(possible_targets)

        # Keep trying to follow until we reach f_desired
        while U["f_current"] < U["f_desired"] and len(possible_targets) > 0:
            V = possible_targets.pop()  # pick from the end
            # If V already has enough followers, skip sometimes
            if V["F_current"] >= V["F_desired"]:
                # V is "full" or nearly full
                # We'll give it a small chance anyway - comment out if you want no chance
                pass  

            # Calculate base probability
            p = base_probability(U, V, 
                                 p_intra_faction, p_inter_faction, 
                                 p_intra_country, p_inter_country, 
                                 hub_multiplier)

            # Adjust if V is near or above its follower limit
            if V["F_current"] >= V["F_desired"]:
                # drastically reduce p
                p *= 0.2
            else:
                # If V is far below target, slight boost
                shortfall_ratio = (V["F_desired"] - V["F_current"]) / max(1, V["F_desired"])
                p += p * 0.2 * shortfall_ratio

            r = random.random()
            if r < p:
                # create follow edge
                edges.append((U["Handle"], V["Handle"]))
                U["f_current"] += 1
                V["F_current"] += 1

    return edges


def base_probability(U, V, 
                     p_intra_faction, p_inter_faction,
                     p_intra_country, p_inter_country, 
                     hub_multiplier):
    """
    Determine the base probability that U follows V.
    Factors:
     - same faction?
     - same country? (e.g. both have #latvian)
     - hub multiplier
    """
    # Start with faction-based probability
    if U["Faction"] == V["Faction"]:
        p = p_intra_faction
    else:
        p = p_inter_faction

    # Next handle #latvian logic (or any country logic)
    U_latvian = "#latvian" in U["tag_list"]
    V_latvian = "#latvian" in V["tag_list"]
    if U_latvian and V_latvian:
        p_country = p_intra_country
    else:
        p_country = p_inter_country

    # Combine the two probabilities in a simple way
    # e.g. average them, or take the max, or multiply
    # Here, let's just take the average for demonstration
    p = (p + p_country) / 2.0

    # If V is a hub, multiply
    if "#hub" in V["tag_list"]:
        p *= hub_multiplier

    return p

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

    # Generate HTML in temp file
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
