import os
import pickle
import numpy as np
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
from typing import List, Optional, Set
from models import Song

class DatabaseManager:
    """Manages the song database and embeddings using numpy for similarity search"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.embeddings_dir = self.data_dir / "embeddings"
        self.embeddings_dir.mkdir(parents=True, exist_ok=True)
        
        print("Initializing embedding model...")
        self.model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
        self.songs = self._load_songs()
        self.embeddings = self._load_embeddings()

    def _load_songs(self) -> dict:
        """Load song database from disk"""
        songs_path = self.embeddings_dir / "songs.pkl"
        if songs_path.exists():
            with open(songs_path, 'rb') as f:
                return pickle.load(f)
        return {}

    def _load_embeddings(self) -> np.ndarray:
        """Load embeddings from disk"""
        embeddings_path = self.embeddings_dir / "embeddings.npy"
        if embeddings_path.exists():
            return np.load(str(embeddings_path))
        return np.array([])

    def _save_state(self):
        """Save current state to disk"""
        # Save songs database
        with open(self.embeddings_dir / "songs.pkl", 'wb') as f:
            pickle.dump(self.songs, f)
        
        # Save embeddings array
        if len(self.embeddings) > 0:
            np.save(str(self.embeddings_dir / "embeddings.npy"), self.embeddings)

    def generate_embedding(self, song: Song) -> np.ndarray:
        """Generate embedding for a song"""
        text = f"{song.name} {song.artist}"
        return self.model.encode([text])[0]

    def add_song(self, song: Song) -> bool:
        """Add a song to the database"""
        if song.id in self.songs:
            return False

        # Generate embedding if not provided
        if song.embedding is None:
            song.embedding = self.generate_embedding(song)
            song.first_added = datetime.now()

        # Add to embeddings array
        embedding = song.embedding.reshape(1, -1)
        if len(self.embeddings) == 0:
            self.embeddings = embedding
        else:
            self.embeddings = np.vstack([self.embeddings, embedding])
        
        # Add to songs database
        self.songs[song.id] = song
        self._save_state()
        return True

    def get_all_songs(self) -> List[Song]:
        """Get all songs in the database"""
        return list(self.songs.values())

    def get_song_by_id(self, song_id: str) -> Optional[Song]:
        """Get a song by its ID"""
        return self.songs.get(song_id)

    def find_similar_songs(self, song: Song, k: int = 1, threshold: float = 0.9) -> List[Song]:
        """Find k most similar songs using cosine similarity"""
        if len(self.embeddings) == 0:
            return []

        if song.embedding is None:
            song.embedding = self.generate_embedding(song)

        # Calculate cosine similarity
        query_embedding = song.embedding.reshape(1, -1)
        similarities = np.dot(self.embeddings, query_embedding.T).flatten()
        similarities = similarities / (np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding))
        
        # Get top k similar songs above threshold
        similar_indices = np.where(similarities > threshold)[0]
        similar_indices = similar_indices[np.argsort(-similarities[similar_indices])][:k]
        
        # Convert to song objects
        similar_songs = []
        song_ids = list(self.songs.keys())
        for idx in similar_indices:
            if idx < len(song_ids) and song_ids[idx] != song.id:
                similar_songs.append(self.songs[song_ids[idx]])
        
        return similar_songs

    def get_stats(self):
        """Get database statistics"""
        return {
            "total_songs": len(self.songs),
            "embedding_dimensions": self.embeddings.shape[1] if len(self.embeddings) > 0 else 0,
            "storage_size_mb": (self.embeddings.nbytes / 1024 / 1024) if len(self.embeddings) > 0 else 0
        } 