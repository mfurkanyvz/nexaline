(() => {
    const alpha = (hex, opacity) => {
        const value = String(hex || "#000000").replace("#", "");
        const normalized = value.length === 3
            ? value.split("").map(char => char + char).join("")
            : value.padEnd(6, "0").slice(0, 6);
        const red = Number.parseInt(normalized.slice(0, 2), 16);
        const green = Number.parseInt(normalized.slice(2, 4), 16);
        const blue = Number.parseInt(normalized.slice(4, 6), 16);
        return `rgba(${red}, ${green}, ${blue}, ${opacity})`;
    };

    const createTheme = (id, name, asset, palette) => {
        const primaryColor = palette.primaryColor;
        const secondaryColor = palette.secondaryColor;
        const accentColor = palette.accentColor;
        const textColor = palette.textColor || "#FFFFFF";
        const mutedTextColor = palette.mutedTextColor || "rgba(255,255,255,0.68)";
        const incomingBubbleColor = palette.incomingBubbleColor || alpha(secondaryColor, 0.88);
        const outgoingBubbleColor = palette.outgoingBubbleColor || primaryColor;
        const topPanelColor = palette.topPanelColor || alpha(secondaryColor, 0.78);
        const bottomPanelColor = palette.bottomPanelColor || alpha(secondaryColor, 0.84);
        const inputColor = palette.inputColor || alpha(secondaryColor, 0.72);
        const borderColor = palette.borderColor || alpha(accentColor, 0.28);
        const iconColor = palette.iconColor || accentColor;

        return {
            id,
            name,
            label: name,
            asset: `/static/themes/${asset}`,
            primaryColor,
            secondaryColor,
            accentColor,
            incomingBubbleColor,
            outgoingBubbleColor,
            topPanelColor,
            bottomPanelColor,
            inputColor,
            borderColor,
            iconColor,
            textColor,
            mutedTextColor,
            backgroundColor: secondaryColor,
            backgroundImage: `linear-gradient(180deg, ${alpha(secondaryColor, 0.18)}, ${alpha(secondaryColor, 0.5)}), url("/static/themes/${asset}")`,
            backgroundSize: "cover",
            backgroundRepeat: "no-repeat",
            incoming: incomingBubbleColor,
            outgoing: outgoingBubbleColor,
            incomingText: textColor,
            outgoingText: textColor,
            border: borderColor,
            accent: accentColor
        };
    };

    const definitions = [
        ["current_default", "NexaLine Elite", "current_theme.webp", {
            primaryColor: "#0A84FF", secondaryColor: "#030406", accentColor: "#8B5CF6",
            incomingBubbleColor: "linear-gradient(145deg, rgba(119,122,118,.94), rgba(74,78,82,.94))",
            outgoingBubbleColor: "linear-gradient(145deg, #0A84FF, #0072E6)",
            topPanelColor: "rgba(5,10,18,.84)", bottomPanelColor: "rgba(7,14,24,.9)",
            inputColor: "rgba(13,24,38,.86)", borderColor: "rgba(56,189,248,.24)",
            iconColor: "#7DD3FC", mutedTextColor: "rgba(255,255,255,.66)"
        }],
        ["shadow_glass", "Shadow Glass", "theme_01.webp", {
            primaryColor: "#0A84FF", secondaryColor: "#06101C", accentColor: "#7DD3FC",
            incomingBubbleColor: "rgba(31,41,55,.88)", outgoingBubbleColor: "linear-gradient(145deg,#0A84FF,#0067D8)",
            topPanelColor: "rgba(7,18,31,.8)", bottomPanelColor: "rgba(7,20,34,.88)"
        }],
        ["neon_nexus", "Neon Nexus", "theme_02.webp", {
            primaryColor: "#7C3AED", secondaryColor: "#070414", accentColor: "#22D3EE",
            incomingBubbleColor: "rgba(36,22,61,.9)", outgoingBubbleColor: "linear-gradient(145deg,#7C3AED,#4C1D95)",
            topPanelColor: "rgba(20,8,47,.8)", bottomPanelColor: "rgba(25,8,54,.9)"
        }],
        ["space_warp", "Space Warp", "theme_03.webp", {
            primaryColor: "#8B5CF6", secondaryColor: "#08051A", accentColor: "#A78BFA",
            incomingBubbleColor: "rgba(31,24,55,.9)", outgoingBubbleColor: "linear-gradient(145deg,#7C3AED,#5B21B6)"
        }],
        ["deep_ocean", "Deep Ocean", "theme_04.webp", {
            primaryColor: "#0891B2", secondaryColor: "#02151D", accentColor: "#22D3EE",
            incomingBubbleColor: "rgba(12,42,50,.9)", outgoingBubbleColor: "linear-gradient(145deg,#0891B2,#0E7490)",
            topPanelColor: "rgba(3,31,39,.82)", bottomPanelColor: "rgba(4,40,48,.9)"
        }],
        ["night_city", "Night City", "theme_05.webp", {
            primaryColor: "#4F46E5", secondaryColor: "#07051A", accentColor: "#C026D3",
            incomingBubbleColor: "rgba(29,25,52,.92)", outgoingBubbleColor: "linear-gradient(145deg,#4338CA,#7E22CE)"
        }],
        ["matrix_neo", "Matrix Neo", "theme_06.webp", {
            primaryColor: "#16A34A", secondaryColor: "#020B05", accentColor: "#4ADE80",
            incomingBubbleColor: "rgba(8,30,13,.92)", outgoingBubbleColor: "linear-gradient(145deg,#15803D,#166534)",
            iconColor: "#4ADE80", mutedTextColor: "rgba(187,247,208,.7)"
        }],
        ["carbon_black", "Carbon Black", "theme_07.webp", {
            primaryColor: "#52525B", secondaryColor: "#050505", accentColor: "#E4E4E7",
            incomingBubbleColor: "rgba(28,28,30,.94)", outgoingBubbleColor: "linear-gradient(145deg,#3F3F46,#27272A)",
            iconColor: "#E4E4E7", borderColor: "rgba(255,255,255,.14)"
        }],
        ["arctic_blue", "Arctic Blue", "theme_08.webp", {
            primaryColor: "#3B82F6", secondaryColor: "#0A1B33", accentColor: "#93C5FD",
            incomingBubbleColor: "rgba(29,55,87,.9)", outgoingBubbleColor: "linear-gradient(145deg,#60A5FA,#2563EB)",
            topPanelColor: "rgba(25,55,89,.8)", bottomPanelColor: "rgba(23,62,104,.88)"
        }],
        ["crimson_pulse", "Crimson Pulse", "theme_09.webp", {
            primaryColor: "#B91C1C", secondaryColor: "#160303", accentColor: "#F87171",
            incomingBubbleColor: "rgba(49,15,18,.92)", outgoingBubbleColor: "linear-gradient(145deg,#B91C1C,#7F1D1D)",
            iconColor: "#F87171", borderColor: "rgba(248,113,113,.3)"
        }],
        ["royal_purple", "Royal Purple", "theme_10.webp", {
            primaryColor: "#7E22CE", secondaryColor: "#110520", accentColor: "#C084FC",
            incomingBubbleColor: "rgba(43,20,65,.92)", outgoingBubbleColor: "linear-gradient(145deg,#7E22CE,#581C87)"
        }],
        ["minimal_dark", "Minimal Dark", "theme_11.webp", {
            primaryColor: "#334155", secondaryColor: "#030507", accentColor: "#CBD5E1",
            incomingBubbleColor: "rgba(30,41,59,.9)", outgoingBubbleColor: "linear-gradient(145deg,#334155,#1E293B)",
            iconColor: "#E2E8F0", borderColor: "rgba(226,232,240,.14)"
        }],
        ["sunset_drive", "Sunset Drive", "theme_12.webp", {
            primaryColor: "#EA580C", secondaryColor: "#1C0805", accentColor: "#FB923C",
            incomingBubbleColor: "rgba(56,24,17,.92)", outgoingBubbleColor: "linear-gradient(145deg,#EA580C,#C2410C)",
            iconColor: "#FDBA74", borderColor: "rgba(251,146,60,.3)"
        }],
        ["forest_night", "Forest Night", "theme_13.webp", {
            primaryColor: "#15803D", secondaryColor: "#03110B", accentColor: "#86EFAC",
            incomingBubbleColor: "rgba(17,48,32,.92)", outgoingBubbleColor: "linear-gradient(145deg,#15803D,#166534)"
        }],
        ["midnight_rain", "Midnight Rain", "theme_14.webp", {
            primaryColor: "#0369A1", secondaryColor: "#03111F", accentColor: "#7DD3FC",
            incomingBubbleColor: "rgba(15,42,67,.92)", outgoingBubbleColor: "linear-gradient(145deg,#0369A1,#075985)"
        }],
        ["retro_terminal", "Retro Terminal", "theme_15.webp", {
            primaryColor: "#166534", secondaryColor: "#010603", accentColor: "#86EFAC",
            incomingBubbleColor: "rgba(3,24,10,.94)", outgoingBubbleColor: "rgba(10,55,23,.94)",
            iconColor: "#86EFAC", textColor: "#DCFCE7", mutedTextColor: "rgba(134,239,172,.68)"
        }],
        ["candy_neon", "Candy Neon", "theme_16.webp", {
            primaryColor: "#DB2777", secondaryColor: "#160313", accentColor: "#F9A8D4",
            incomingBubbleColor: "rgba(58,15,48,.92)", outgoingBubbleColor: "linear-gradient(145deg,#DB2777,#9D174D)"
        }],
        ["ice_crystal", "Ice Crystal", "theme_17.webp", {
            primaryColor: "#0284C7", secondaryColor: "#03182B", accentColor: "#BAE6FD",
            incomingBubbleColor: "rgba(19,57,83,.92)", outgoingBubbleColor: "linear-gradient(145deg,#0EA5E9,#0369A1)",
            iconColor: "#BAE6FD"
        }],
        ["volcano_fire", "Volcano Fire", "theme_18.webp", {
            primaryColor: "#DC2626", secondaryColor: "#160302", accentColor: "#FB923C",
            incomingBubbleColor: "rgba(59,15,12,.92)", outgoingBubbleColor: "linear-gradient(145deg,#DC2626,#991B1B)"
        }],
        ["golden_luxury", "Golden Luxury", "theme_19.webp", {
            primaryColor: "#A16207", secondaryColor: "#120D03", accentColor: "#FACC15",
            incomingBubbleColor: "rgba(55,42,12,.92)", outgoingBubbleColor: "linear-gradient(145deg,#A16207,#854D0E)",
            iconColor: "#FDE68A", borderColor: "rgba(250,204,21,.3)"
        }],
        ["nebula_dream", "Nebula Dream", "theme_20.webp", {
            primaryColor: "#9333EA", secondaryColor: "#10031D", accentColor: "#D8B4FE",
            incomingBubbleColor: "rgba(49,18,72,.92)", outgoingBubbleColor: "linear-gradient(145deg,#9333EA,#6B21A8)"
        }],
        ["galatasaray", "Galatasaray", "theme_21.webp", {
            primaryColor: "#A90432", secondaryColor: "#210304", accentColor: "#F59E0B",
            incomingBubbleColor: "rgba(68,13,16,.94)", outgoingBubbleColor: "linear-gradient(145deg,#A90432,#7F1D1D)",
            iconColor: "#FBBF24", borderColor: "rgba(245,158,11,.34)"
        }],
        ["fenerbahce", "Fenerbahçe", "theme_22.webp", {
            primaryColor: "#0B2A6F", secondaryColor: "#041027", accentColor: "#FACC15",
            incomingBubbleColor: "rgba(11,35,75,.94)", outgoingBubbleColor: "linear-gradient(145deg,#0B2A6F,#071C4A)",
            iconColor: "#FDE047", borderColor: "rgba(250,204,21,.34)"
        }],
        ["besiktas", "Beşiktaş", "theme_23.webp", {
            primaryColor: "#27272A", secondaryColor: "#050505", accentColor: "#F4F4F5",
            incomingBubbleColor: "rgba(31,31,35,.94)", outgoingBubbleColor: "linear-gradient(145deg,#3F3F46,#18181B)",
            iconColor: "#FFFFFF", borderColor: "rgba(220,38,38,.28)"
        }],
        ["trabzonspor", "Trabzonspor", "theme_24.webp", {
            primaryColor: "#7F1D3A", secondaryColor: "#18050C", accentColor: "#38BDF8",
            incomingBubbleColor: "rgba(67,18,35,.94)", outgoingBubbleColor: "linear-gradient(145deg,#7F1D3A,#58152B)",
            iconColor: "#7DD3FC", borderColor: "rgba(56,189,248,.3)"
        }],
        ["turkculuk", "Türkçülük", "theme_25.webp", {
            primaryColor: "#0E7490", secondaryColor: "#02171C", accentColor: "#67E8F9",
            incomingBubbleColor: "rgba(13,48,57,.94)", outgoingBubbleColor: "linear-gradient(145deg,#0E7490,#155E75)",
            iconColor: "#A5F3FC"
        }],
        ["bozkurt", "Bozkurt", "theme_26.webp", {
            primaryColor: "#52525B", secondaryColor: "#080808", accentColor: "#D4D4D8",
            incomingBubbleColor: "rgba(37,37,41,.94)", outgoingBubbleColor: "linear-gradient(145deg,#52525B,#27272A)",
            iconColor: "#E4E4E7", borderColor: "rgba(212,212,216,.2)"
        }]
    ];

    window.NEXALINE_CHAT_THEME_DEFAULT = "current_default";
    window.NEXALINE_CHAT_THEMES = Object.fromEntries(
        definitions.map(([id, name, asset, palette]) => [id, createTheme(id, name, asset, palette)])
    );
})();
