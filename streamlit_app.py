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
       - **Name**  
       - **Handle**  (unique identifier)  
       - **Faction**  
       - **Tags**  
       - **TwHandle** (optional)  
       - **TwFollowers** (desired # of followers)  
       - **TwFollowing** (desired # of followings)  

    2. Set the probabilities below:
       - **Country-Specific Hub Probability**: if \u201C#hub_xxx\u201D in V's tags and \u201C#xxx\u201D in U's tags  
       - **Global Hub Probability**: if V is tagged as \u201C#hub\u201D (international hub)  
       - **Intra-Faction Probability**: if neither is a hub, but U and V share the same faction  
       - **Inter-Faction Probability**: if neither is a hub and U and V are in different factions  

    3. The app will create a synthetic "who-follows-whom" graph that tries to match each user’s follower/following counts.  

    4. Finally, it ensures that each user follows at least 2 others and is followed by at least 2 others.  

    5. Scroll down to see a table of edges and an interactive network diagram.
    """)

    # Sliders for adjusting probabilities
    hub_country_probability = st.slider(
        "Probability if target has #hub_country (e.g. #hub_uk) and user also has #country (e.g. #uk)",
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
                hub_country_probability,
                hub_global_probability,
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
                          hub_country_probability=0.6,
                          hub_global_probability=0.5,
                          p_intra_faction=0.3, 
                          p_inter_faction=0.1):
    """
    Given a DataFrame with columns:
     - Name, Handle, Faction, Tags, TwHandle, TwFollowers, TwFollowing

    Generate a synthetic social graph using a probabilistic approach:
      1) If target (V) has a country-specific hub tag, e.g. #hub_uk,
         and user (U) has #uk, use hub_country_probability.
      2) Else if target (V) is a global hub (#hub),
         use hub_global_probability.
      3) Else if same faction, use p_intra_faction.
      4) Otherwise, use p_inter_faction.

    Also tries to respect each user's TwFollowers & TwFollowing counts,
    then ensures everyone has at least 2 followers & 2 followings.
    """

    # Convert to a list of dicts for easier handling
    personas = df.to_dict("records")

    # Prepare storage for dynamic counts
    for person in personas:
        person["F_desired"]  = int(person["TwFollowers"])
        person["f_desired"]  = int(person["TwFollowing"])
        person["F_current"]  = 0  # how many followers they currently have
        person["f_current"]  = 0  # how many they currently follow
        # Split tags into a list, e.g. "#hub #uk" => ["#hub", "#uk"]
        person["tag_list"]   = str(person["Tags"]).lower().split()

    # Shuffle to add randomness to iteration order
    random.shuffle(personas)

    # We'll store edges as tuples: (follower_handle, followed_handle)
    edges = []

    # 1) MAIN GENERATION: For each user U, pick who they follow
    for U in personas:
        # potential targets
        possible_targets = [p for p in personas if p["Handle"] != U["Handle"]]
        random.shuffle(possible_targets)

        # Keep trying until we reach f_desired
        while U["f_current"] < U["f_desired"] and possible_targets:
            V = possible_targets.pop()  # pick from the end

            # Calculate base probability
            p = base_probability(U, V,
                                 hub_country_probability,
                                 hub_global_probability,
                                 p_intra_faction,
                                 p_inter_faction)

            # Adjust if V is at or above its desired follower count
            if V["F_current"] >= V["F_desired"]:
                # drastically reduce p, but not to zero
                p *= 0.2
            else:
                # slight boost if V is still below desired
                shortfall_ratio = (V["F_desired"] - V["F_current"]) / max(1, V["F_desired"])
                p += p * 0.2 * shortfall_ratio

            # Random draw
            r = random.random()
            if r < p:
                # U follows V
                edges.append((U["Handle"], V["Handle"]))
                U["f_current"]  += 1
                V["F_current"]  += 1

    # 2) FINAL FIX: Ensure everyone has >=2 followers and >=2 followings
    edges = ensure_minimum_two(personas, edges)

    return edges


def base_probability(U, V, 
                     hub_country_probability,
                     hub_global_probability, 
                     p_intra_faction,
                     p_inter_faction):
    """
    Determine the base probability that U follows V.
    Hierarchy of rules:

      1) If V has a country-specific hub tag "#hub_xxx":
         - If U has the matching "#xxx" tag, use hub_country_probability.
         - Otherwise, fallback to normal faction logic below.

      2) Else if V has a global hub tag "#hub",
         use hub_global_probability.

      3) Else if U and V share the same Faction,
         use p_intra_faction.

      4) Otherwise, use p_inter_faction.
    """

    # 1) Check for a country-specific hub tag (#hub_xxx)
    hub_country_tag = find_country_hub_tag(V["tag_list"])
    if hub_country_tag is not None:
        # e.g. hub_country_tag == 'uk' if V has '#hub_uk'
        if f"#{hub_country_tag}" in U["tag_list"]:
            return hub_country_probability
        # Otherwise, we continue to the next checks

    # 2) Check if V is a global hub (#hub)
    if "#hub" in V["tag_list"]:
        return hub_global_probability

    # 3) Check faction
    if U["Faction"] == V["Faction"]:
        return p_intra_faction

    # 4) Otherwise, inter-faction
    return p_inter_faction


def find_country_hub_tag(tag_list):
    """
    Look for a tag of the form '#hub_xxx' in the given list.
    Returns the 'xxx' part if found, otherwise None.
    """
    for tag in tag_list:
        if tag.startswith("#hub_") and len(tag) > 5:
            # e.g. tag might be '#hub_uk' => return 'uk'
            return tag.split("_", 1)[1]
    return None


#############################
# 3. FINAL FIX LOGIC        #
#############################

def ensure_minimum_two(personas, edges):
    """
    Ensure that each persona follows at least 2 others and
    is followed by at least 2 others.

    1) Build adjacency from edges.
    2) For each persona, if they have <2 followings or <2 followers,
       we try to fix by:
         - "Following back" someone who follows them, or
         - Randomly following someone else, or
         - Randomly letting others follow them if they have <2 followers.
    3) Repeat until all have >=2 or we reach a max iteration limit.
    """

    # Convert list of edges into adjacency dict for quick lookups
    # out_edges[u] = set of v that u follows
    # in_edges[v]  = set of u that follow v
    out_edges = {}
    in_edges = {}
    for p in personas:
        out_edges[p["Handle"]] = set()
        in_edges[p["Handle"]] = set()

    for (u, v) in edges:
        out_edges[u].add(v)
        in_edges[v].add(u)

    # We'll do multiple passes until stable or max tries
    max_tries = 1000
    tries = 0
    n = len(personas)

    while tries < max_tries:
        tries += 1
        changed = False

        for p in personas:
            me  = p["Handle"]
            f_count = len(out_edges[me])  # how many I'm following
            F_count = len(in_edges[me])   # how many follow me

            # Fix: need at least 2 followings
            if f_count < 2:
                # 1) If any follower is not yet followed back, do a follow back
                potential_follow_backs = [follower for follower in in_edges[me]
                                          if me not in out_edges[follower]]  
                # follower is in_edges[me] -> that means they follow me
                # out_edges[follower] doesn't contain me -> means I don't follow them yet

                if potential_follow_backs:
                    target = random.choice(potential_follow_backs)
                    out_edges[me].add(target)
                    in_edges[target].add(me)
                    changed = True
                else:
                    # 2) Otherwise pick a random new target from the entire set
                    # that is not me and not already followed
                    all_handles = [x["Handle"] for x in personas if x["Handle"] != me]
                    random_target = random.choice(all_handles)
                    if random_target not in out_edges[me]:
                        out_edges[me].add(random_target)
                        in_edges[random_target].add(me)
                        changed = True

            # Fix: need at least 2 followers
            if F_count < 2:
                # Let someone follow me if possible
                # 1) Maybe "follow back" one I follow but who doesn't follow me?
                potential_followers = [x for x in out_edges[me]
                                       if me not in out_edges[x]]  
                # x in out_edges[me] means I follow x
                # out_edges[x] is who x follows
                # if me not in out_edges[x], that means x doesn't follow me

                if potential_followers:
                    target = random.choice(potential_followers)
                    out_edges[target].add(me)
                    in_edges[me].add(target)
                    changed = True
                else:
                    # 2) Or pick a random user to follow me
                    all_handles = [x["Handle"] for x in personas if x["Handle"] != me]
                    random_user = random.choice(all_handles)
                    if me not in out_edges[random_user]:
                        out_edges[random_user].add(me)
                        in_edges[me].add(random_user)
                        changed = True

        if not changed:
            break  # no more changes needed

    # Rebuild the edge list
    final_edges = []
    for u in out_edges:
        for v in out_edges[u]:
            final_edges.append((u, v))

    return final_edges


#############################
# 4. NETWORK DIAGRAM        #
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
# 5. RUN THE APP            #
#############################

if __name__ == "__main__":
    main()
